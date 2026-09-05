"""Tests for the SQS-buffered path's two Lambda handlers (enqueue_job,
process_job) against moto-mocked DynamoDB/SQS -- no real AWS account, no
network call, no real Bedrock call (mock narrator, same as every other
handler test in this suite).
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lambda_src"))

import enqueue_job  # noqa: E402
import process_job  # noqa: E402

_TABLE_NAME = os.environ["RUNS_TABLE_NAME"]


@pytest.fixture()
def aws_resources():
    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName=_TABLE_NAME,
            KeySchema=[{"AttributeName": "run_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "run_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        sqs = boto3.client("sqs", region_name="us-east-1")
        queue_url = sqs.create_queue(QueueName="test-jobs-queue")["QueueUrl"]

        enqueue_job._dynamodb_resource = None
        enqueue_job._sqs_client = None
        process_job._dynamodb_resource = None
        with patch.dict(os.environ, {"JOBS_QUEUE_URL": queue_url}):
            yield queue_url


_DEFAULT_CALLER_SUB = "cognito-sub-caller-1"


def _api_gateway_event(body: dict, sub: str = _DEFAULT_CALLER_SUB) -> dict:
    return {"body": json.dumps(body), "requestContext": {"authorizer": {"jwt": {"claims": {"sub": sub}}}}}


# -- enqueue_job ----------------------------------------------------------
def test_enqueue_returns_202_and_writes_queued_record(aws_resources):
    event = _api_gateway_event({"user_id": "user_demo_001", "question": "hello", "run_id": "job-1"})
    result = enqueue_job.handler(event, None)

    assert result["statusCode"] == 202
    body = json.loads(result["body"])
    assert body["run_id"] == "job-1"
    assert body["status"] == "QUEUED"

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    item = table.get_item(Key={"run_id": "job-1"})["Item"]
    assert item["status"] == "QUEUED"
    assert item["user_id"] == "user_demo_001"
    assert item["owner_sub"] == _DEFAULT_CALLER_SUB
    assert item["execution_type"] == "SQS"


def test_enqueue_refuses_to_overwrite_a_run_id_collision(aws_resources):
    """Regression test: an independent review found that /ask, /runs (Step
    Functions), and /jobs (SQS) share the same run_id keyspace, and this
    handler's plain put_item used to silently overwrite whatever another
    path had already written. Fixed with a conditional create."""
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    table.put_item(Item={"run_id": "collided", "status": "RUNNING", "owner_sub": "someone-else", "execution_type": "SYNC"})

    event = _api_gateway_event({"user_id": "user_demo_001", "question": "hello", "run_id": "collided"})
    result = enqueue_job.handler(event, None)

    assert result["statusCode"] == 409
    item = table.get_item(Key={"run_id": "collided"})["Item"]
    assert item["status"] == "RUNNING"
    assert item["owner_sub"] == "someone-else"


def test_enqueue_sends_a_message_to_sqs(aws_resources):
    queue_url = aws_resources
    event = _api_gateway_event({"user_id": "user_demo_001", "question": "hello", "run_id": "job-2"})
    enqueue_job.handler(event, None)

    sqs = boto3.client("sqs", region_name="us-east-1")
    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", [])
    assert len(messages) == 1
    body = json.loads(messages[0]["Body"])
    assert body == {"run_id": "job-2", "user_id": "user_demo_001", "question": "hello"}


def test_enqueue_generates_run_id_when_not_supplied(aws_resources):
    event = _api_gateway_event({"user_id": "user_demo_001", "question": "hello"})
    result = enqueue_job.handler(event, None)
    body = json.loads(result["body"])
    assert body["run_id"]


@pytest.mark.parametrize(
    "body",
    [
        {"user_id": 12345, "question": "hello"},
        {"user_id": "user_demo_001", "question": 12345},
        {"user_id": "user_demo_001", "question": "hello", "run_id": 12345},
        {"question": "hello"},
        {"user_id": "user_demo_001"},
    ],
    ids=["int_user_id", "int_question", "int_run_id", "missing_user_id", "missing_question"],
)
def test_enqueue_rejects_invalid_input_with_400(aws_resources, body):
    result = enqueue_job.handler(_api_gateway_event(body), None)
    assert result["statusCode"] == 400


@pytest.mark.parametrize("bad_run_id", ["has spaces", "has/slash", "x" * 81])
def test_enqueue_rejects_invalid_run_id_characters(aws_resources, bad_run_id):
    event = _api_gateway_event({"user_id": "user_demo_001", "question": "hello", "run_id": bad_run_id})
    result = enqueue_job.handler(event, None)
    assert result["statusCode"] == 400


def test_enqueue_writes_failed_status_instead_of_orphaning_the_record_when_send_fails(aws_resources):
    """Regression test: an independent review found that a send_message
    failure after the DynamoDB record was already created left it QUEUED
    forever with no message ever coming. Fixed with a compensating write."""
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    event = _api_gateway_event({"user_id": "user_demo_001", "question": "hello", "run_id": "job-send-fail"})

    with patch("enqueue_job._sqs") as mock_sqs_fn:
        mock_sqs_fn.return_value.send_message.side_effect = RuntimeError("simulated SQS outage")
        result = enqueue_job.handler(event, None)

    assert result["statusCode"] == 500
    item = table.get_item(Key={"run_id": "job-send-fail"})["Item"]
    assert item["status"] == "FAILED"
    assert "error_message" in item


def test_enqueue_invalid_json_body_returns_400(aws_resources):
    result = enqueue_job.handler({"body": "{not valid json"}, None)
    assert result["statusCode"] == 400


# -- process_job ------------------------------------------------------------
def _sqs_event(run_id: str, user_id: str, question: str) -> dict:
    return {"Records": [{"body": json.dumps({"run_id": run_id, "user_id": user_id, "question": question})}]}


def test_process_job_writes_succeeded_result(aws_resources):
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    table.put_item(Item={"run_id": "job-3", "status": "QUEUED"})

    process_job.handler(_sqs_event("job-3", "user_demo_001", "What should I focus on first?"), None)

    item = table.get_item(Key={"run_id": "job-3"})["Item"]
    assert item["status"] == "SUCCEEDED"
    assert item["safe"] is True
    assert item["narrator_backend"] == "mock"
    assert "162" in item["answer"]


def test_process_job_marks_running_before_finishing(aws_resources):
    """A crude but real check that the intermediate RUNNING write actually
    happens (not just skipped straight to the terminal write) -- patches
    the table's update_item to record every status transition seen."""
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    table.put_item(Item={"run_id": "job-4", "status": "QUEUED"})

    seen_statuses = []
    real_update_item = table.update_item

    def _spy_update_item(**kwargs):
        seen_statuses.append(kwargs["ExpressionAttributeValues"][":status"])
        return real_update_item(**kwargs)

    with patch("process_job._dynamodb") as mock_dynamodb_fn:
        mock_dynamodb_fn.return_value.Table.return_value.update_item.side_effect = _spy_update_item
        process_job.handler(_sqs_event("job-4", "user_demo_001", "hello"), None)

    assert seen_statuses[0] == "RUNNING"
    assert seen_statuses[-1] == "SUCCEEDED"


def test_process_job_writes_failed_result_for_unknown_user(aws_resources):
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    table.put_item(Item={"run_id": "job-5", "status": "QUEUED"})

    process_job.handler(_sqs_event("job-5", "no_such_user", "hello"), None)

    item = table.get_item(Key={"run_id": "job-5"})["Item"]
    assert item["status"] == "FAILED"
    assert "error_message" in item


def test_process_job_does_not_reopen_or_reprocess_an_already_cancelled_job(aws_resources):
    """Regression test: an independent review found that SQS redelivery
    (at-least-once delivery -- the first attempt's Lambda might have timed
    out, or its ack might not have landed before the visibility timeout
    expired) used to unconditionally set the record back to RUNNING and
    call the agent again, even if cancel_run.py had already marked it
    CANCELLED in the meantime. Fixed: the RUNNING write is now conditional
    on the record's *current* status, and a cancelled job is left alone."""
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    table.put_item(Item={"run_id": "job-6", "status": "CANCELLED", "completed_at": "2026-01-01T00:00:00+00:00"})

    with patch("process_job._agent") as mock_agent:
        process_job.handler(_sqs_event("job-6", "user_demo_001", "hello"), None)
        mock_agent.ask.assert_not_called()

    item = table.get_item(Key={"run_id": "job-6"})["Item"]
    assert item["status"] == "CANCELLED"


def test_process_job_final_write_does_not_clobber_a_cancellation_that_raced_in_mid_processing(aws_resources):
    """Regression test, the other half of the race: if cancel_run.py wins
    the race *while* this handler is mid-`agent.ask()` call (so the
    initial RUNNING write already succeeded), the final SUCCEEDED write
    must not blindly overwrite the CANCELLED status that landed in
    between."""
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    table.put_item(Item={"run_id": "job-7", "status": "QUEUED"})

    original_ask = process_job._agent.ask

    def _ask_then_get_cancelled(*args, **kwargs):
        # Simulate cancel_run.py winning the race for this run_id in the
        # exact window between this handler's RUNNING write and its own
        # final write, by mutating the record out from under it here.
        table.update_item(
            Key={"run_id": "job-7"},
            UpdateExpression="SET #status = :cancelled",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":cancelled": "CANCELLED"},
        )
        return original_ask(*args, **kwargs)

    with patch.object(process_job._agent, "ask", side_effect=_ask_then_get_cancelled):
        process_job.handler(_sqs_event("job-7", "user_demo_001", "What should I focus on first?"), None)

    item = table.get_item(Key={"run_id": "job-7"})["Item"]
    assert item["status"] == "CANCELLED"
    assert "answer" not in item
