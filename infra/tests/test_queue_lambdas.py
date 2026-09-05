"""Tests for the SQS-buffered path's three Lambda handlers (enqueue_job,
process_job, reconcile_dlq) against moto-mocked DynamoDB/SQS -- no real
AWS account, no network call, no real Bedrock call (mock narrator, same
as every other handler test in this suite).
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lambda_src"))

import enqueue_job  # noqa: E402
import process_job  # noqa: E402
import reconcile_dlq  # noqa: E402
import run_writes  # noqa: E402

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
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=os.environ["EVIDENCE_BUCKET_NAME"])

        enqueue_job._dynamodb_resource = None
        enqueue_job._sqs_client = None
        process_job._s3_client = None
        run_writes._dynamodb_resource = None
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


def test_enqueue_compensating_write_does_not_clobber_a_job_a_consumer_already_finished(aws_resources):
    """Regression test: a second independent review found that the
    compensating write above was unconditional -- but an SDK exception
    from send_message doesn't prove SQS rejected the message. It can mean
    the send actually succeeded and only the *response* was lost (e.g. a
    read timeout), in which case a consumer can already be processing --
    or have finished -- the job by the time this handler's except block
    runs. An unconditional overwrite would clobber that real outcome back
    to FAILED. Simulated here by having a consumer finish the job (write
    SUCCEEDED) *before* the compensating write's conditional update runs;
    the fix (conditioned on the record still being QUEUED) must leave that
    SUCCEEDED record alone."""
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    event = _api_gateway_event({"user_id": "user_demo_001", "question": "hello", "run_id": "job-ambiguous-send"})

    def _consumer_finishes_then_raise(*args, **kwargs):
        table.update_item(
            Key={"run_id": "job-ambiguous-send"},
            UpdateExpression="SET #status = :s, answer = :a, safe = :safe",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":s": "SUCCEEDED", ":a": "a real answer", ":safe": True},
        )
        raise RuntimeError("simulated response-loss timeout after the message actually sent")

    with patch("enqueue_job._sqs") as mock_sqs_fn:
        mock_sqs_fn.return_value.send_message.side_effect = _consumer_finishes_then_raise
        result = enqueue_job.handler(event, None)

    assert result["statusCode"] == 500
    item = table.get_item(Key={"run_id": "job-ambiguous-send"})["Item"]
    assert item["status"] == "SUCCEEDED"
    assert item["answer"] == "a real answer"


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


def test_process_job_persists_full_trace_to_s3(aws_resources):
    """Regression test: same fix as agent_task.py's -- until this, the
    SQS-buffered path had no full grounding trace anywhere either,
    written to the same {run_id}.json key the other two paths use."""
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    table.put_item(Item={"run_id": "job-trace-1", "status": "QUEUED"})

    process_job.handler(_sqs_event("job-trace-1", "user_demo_001", "What should I focus on first?"), None)

    obj = boto3.client("s3", region_name="us-east-1").get_object(Bucket=os.environ["EVIDENCE_BUCKET_NAME"], Key="job-trace-1.json")
    trace = json.loads(obj["Body"].read())
    assert trace["intent"] == "priority_focus"
    assert len(trace["grounded_facts"]) > 0


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

    with patch("run_writes._dynamodb") as mock_dynamodb_fn:
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


def test_process_job_rejects_a_concurrent_delivery_while_the_lease_is_still_valid(aws_resources):
    """Regression test: a second independent review found that the plain
    "status in (QUEUED, RUNNING)" condition let two genuinely concurrent
    deliveries for the same run_id (a redelivery, or a second message a
    client's own retry against enqueue_job.py created) both pass and both
    call the agent, doubling Bedrock cost and racing on the terminal
    write. Simulated here by seeding a record already RUNNING with a
    processing lease that hasn't expired yet -- the handler must skip it
    without ever calling the agent."""
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    future_lease = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    table.put_item(Item={"run_id": "job-8", "status": "RUNNING", "processing_lease_expires_at": future_lease})

    with patch("process_job._agent") as mock_agent:
        process_job.handler(_sqs_event("job-8", "user_demo_001", "hello"), None)
        mock_agent.ask.assert_not_called()

    item = table.get_item(Key={"run_id": "job-8"})["Item"]
    assert item["status"] == "RUNNING"
    assert item["processing_lease_expires_at"] == future_lease


def test_process_job_reclaims_processing_once_the_prior_lease_has_expired(aws_resources):
    """The other half of the lease fix: a record stuck RUNNING because its
    prior processor crashed without ever writing a terminal state must
    still be recoverable once enough time has passed that the prior
    attempt is provably no longer running (its lease has expired) --
    otherwise every crash would strand the run forever."""
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    expired_lease = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    table.put_item(Item={"run_id": "job-9", "status": "RUNNING", "processing_lease_expires_at": expired_lease})

    process_job.handler(_sqs_event("job-9", "user_demo_001", "What should I focus on first?"), None)

    item = table.get_item(Key={"run_id": "job-9"})["Item"]
    assert item["status"] == "SUCCEEDED"
    assert item["safe"] is True


# -- reconcile_dlq ------------------------------------------------------------
def _dlq_event(run_id: str) -> dict:
    return {"Records": [{"body": json.dumps({"run_id": run_id, "user_id": "user_demo_001", "question": "hello"})}]}


def test_reconcile_dlq_marks_a_stuck_queued_run_failed(aws_resources):
    """Regression test: a second independent review found that a run whose
    message exceeded process_job.py's max delivery attempts and landed in
    the DLQ had nothing left in the system that would ever write it a
    terminal status -- a caller polling GET /runs/{run_id} would wait
    forever. Simulates every process_job.py attempt dying before its
    first RUNNING write (the record never left QUEUED)."""
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    table.put_item(Item={"run_id": "job-dlq-1", "status": "QUEUED"})

    reconcile_dlq.handler(_dlq_event("job-dlq-1"), None)

    item = table.get_item(Key={"run_id": "job-dlq-1"})["Item"]
    assert item["status"] == "FAILED"
    assert "error_message" in item


def test_reconcile_dlq_marks_a_stuck_running_run_failed(aws_resources):
    """The other stuck state: an attempt died mid-processing (after its
    RUNNING write, before any terminal write)."""
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    table.put_item(Item={"run_id": "job-dlq-2", "status": "RUNNING"})

    reconcile_dlq.handler(_dlq_event("job-dlq-2"), None)

    item = table.get_item(Key={"run_id": "job-dlq-2"})["Item"]
    assert item["status"] == "FAILED"


def test_reconcile_dlq_does_not_clobber_a_run_that_actually_succeeded(aws_resources):
    """Race safety: a message can land in the DLQ (its Nth delivery failed)
    even though an *earlier* delivery already succeeded and the queue
    just hadn't deleted the message yet, or a redrive lands right as the
    real outcome is written. The reconciler must never overwrite an
    already-terminal record."""
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)
    table.put_item(Item={"run_id": "job-dlq-3", "status": "SUCCEEDED", "answer": "a real answer", "safe": True})

    reconcile_dlq.handler(_dlq_event("job-dlq-3"), None)

    item = table.get_item(Key={"run_id": "job-dlq-3"})["Item"]
    assert item["status"] == "SUCCEEDED"
    assert item["answer"] == "a real answer"
