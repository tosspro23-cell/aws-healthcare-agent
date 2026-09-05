"""Tests for the Phase 3 Lambda handlers (mark_running, agent_task,
record_result, start_run, get_run, cancel_run) -- all against moto-mocked
DynamoDB/Step Functions, no real AWS account, no network call.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lambda_src"))

import agent_task  # noqa: E402
import cancel_run  # noqa: E402
import get_run  # noqa: E402
import mark_running  # noqa: E402
import record_result  # noqa: E402
import start_run  # noqa: E402

_TABLE_NAME = os.environ["RUNS_TABLE_NAME"]
_DEFAULT_CALLER_SUB = "cognito-sub-caller-1"
_OTHER_CALLER_SUB = "cognito-sub-caller-2"


def _api_event(path_params: dict | None = None, body: str | None = None, sub: str = _DEFAULT_CALLER_SUB) -> dict:
    event: dict = {"requestContext": {"authorizer": {"jwt": {"claims": {"sub": sub}}}}}
    if path_params is not None:
        event["pathParameters"] = path_params
    if body is not None:
        event["body"] = body
    return event


@pytest.fixture()
def runs_table():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=_TABLE_NAME,
            KeySchema=[{"AttributeName": "run_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "run_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield boto3.resource("dynamodb", region_name="us-east-1").Table(_TABLE_NAME)


@pytest.fixture()
def state_machine_arn():
    with mock_aws():
        client = boto3.client("stepfunctions", region_name="us-east-1")
        response = client.create_state_machine(
            name="test-agent-run-sm",
            definition='{"StartAt":"Pass","States":{"Pass":{"Type":"Pass","End":true}}}',
            roleArn="arn:aws:iam::123456789012:role/test-role",
        )
        yield response["stateMachineArn"]


# -- mark_running -------------------------------------------------------
def test_mark_running_writes_running_item(runs_table):
    event = {"run_id": "r1", "user_id": "user_demo_001", "question": "hi", "owner_sub": _DEFAULT_CALLER_SUB}
    result = mark_running.handler(event, None)
    assert result == {"run_id": "r1", "user_id": "user_demo_001", "question": "hi"}
    item = runs_table.get_item(Key={"run_id": "r1"})["Item"]
    assert item["status"] == "RUNNING"
    assert item["user_id"] == "user_demo_001"
    assert item["owner_sub"] == _DEFAULT_CALLER_SUB
    assert item["execution_type"] == "STEP_FUNCTIONS"
    assert "started_at" in item


def test_mark_running_refuses_to_overwrite_a_run_id_collision(runs_table):
    """Regression test: an independent review found that /ask, /runs (Step
    Functions), and /jobs (SQS) share the same run_id keyspace, and this
    handler's plain put_item used to silently overwrite whatever another
    path had already written. Fixed with a conditional create."""
    runs_table.put_item(Item={"run_id": "collided", "status": "QUEUED", "owner_sub": "someone-else", "execution_type": "SQS"})
    event = {"run_id": "collided", "user_id": "user_demo_001", "question": "hi", "owner_sub": _DEFAULT_CALLER_SUB}

    with pytest.raises(RuntimeError, match="already in use"):
        mark_running.handler(event, None)

    item = runs_table.get_item(Key={"run_id": "collided"})["Item"]
    assert item["status"] == "QUEUED"
    assert item["owner_sub"] == "someone-else"


# -- agent_task -----------------------------------------------------------
def test_agent_task_returns_grounded_answer():
    result = agent_task.handler({"run_id": "r1", "user_id": "user_demo_001", "question": "What should I focus on first?"}, None)
    assert result["safe"] is True
    assert "162" in result["answer"]
    assert result["trace"]["intent"] == "priority_focus"


def test_agent_task_propagates_exceptions_for_unknown_user():
    """Deliberately does NOT catch this -- Step Functions' Catch block is
    supposed to see it (see agent_task.py's module docstring)."""
    from care_agent.data_store import UnknownUserError

    with pytest.raises(UnknownUserError):
        agent_task.handler({"run_id": "r1", "user_id": "nobody", "question": "hi"}, None)


# -- record_result --------------------------------------------------------
def test_record_result_finalizes_success(runs_table):
    runs_table.put_item(Item={"run_id": "r1", "status": "RUNNING"})
    event = {"run_id": "r1", "outcome": "SUCCEEDED", "answer": "the answer", "safe": True, "narrator_backend": "bedrock"}
    result = record_result.handler(event, None)
    assert result["finalized_by_this_step"] is True
    item = runs_table.get_item(Key={"run_id": "r1"})["Item"]
    assert item["status"] == "SUCCEEDED"
    assert item["answer"] == "the answer"
    assert item["safe"] is True
    assert item["narrator_backend"] == "bedrock"


def test_record_result_finalizes_failure(runs_table):
    runs_table.put_item(Item={"run_id": "r1", "status": "RUNNING"})
    result = record_result.handler({"run_id": "r1", "outcome": "FAILED", "error": "boom"}, None)
    assert result["finalized_by_this_step"] is True
    item = runs_table.get_item(Key={"run_id": "r1"})["Item"]
    assert item["status"] == "FAILED"
    assert item["error_message"] == "boom"


def test_record_result_loses_race_gracefully_when_already_finalized(runs_table):
    """The core Phase 3 concurrency scenario: something else (e.g.
    cancel_run.py) already finalized this run. record_result must not
    raise or overwrite -- it should report that it lost the race."""
    runs_table.put_item(Item={"run_id": "r1", "status": "CANCELLED"})
    event = {"run_id": "r1", "outcome": "SUCCEEDED", "answer": "too late", "safe": True, "narrator_backend": "bedrock"}
    result = record_result.handler(event, None)
    assert result["finalized_by_this_step"] is False
    item = runs_table.get_item(Key={"run_id": "r1"})["Item"]
    assert item["status"] == "CANCELLED"  # unchanged -- the winner's write stands


# -- start_run --------------------------------------------------------------
def test_start_run_starts_execution_and_returns_202(state_machine_arn):
    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        start_run._sfn_client = None  # force a fresh client bound to the mocked region
        event = _api_event(body='{"user_id": "user_demo_001", "question": "hello"}')
        result = start_run.handler(event, None)
    assert result["statusCode"] == 202
    import json

    body = json.loads(result["body"])
    assert body["status"] == "RUNNING"
    assert body["run_id"]


def test_start_run_threads_owner_sub_into_the_execution_input(state_machine_arn):
    """owner_sub (the authenticated caller's JWT sub) must reach
    mark_running.py via the Step Functions execution input -- that's the
    only way the async path can enforce run ownership on read/cancel."""
    import json

    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        start_run._sfn_client = None
        event = _api_event(body='{"user_id": "user_demo_001", "question": "hello", "run_id": "owned-run"}', sub=_OTHER_CALLER_SUB)
        start_run.handler(event, None)

    sfn = boto3.client("stepfunctions", region_name="us-east-1")
    execution_arn = f"{state_machine_arn.replace(':stateMachine:', ':execution:')}:owned-run"
    execution_input = json.loads(sfn.describe_execution(executionArn=execution_arn)["input"])
    assert execution_input["owner_sub"] == _OTHER_CALLER_SUB


def test_start_run_missing_fields_returns_400(state_machine_arn):
    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        start_run._sfn_client = None
        result = start_run.handler(_api_event(body='{"question": "hello"}'), None)
    assert result["statusCode"] == 400


def test_start_run_non_string_run_id_returns_400_not_500(state_machine_arn):
    """Regression test: a non-string run_id used to reach `start_execution`
    unvalidated -- `name` must be a string, so this raised an uncaught
    boto3 ClientError (the only caught exception was
    ExecutionAlreadyExists) instead of a clean 400. Found via the Phase 4
    stress-test sweep, fixed in start_run.py -- see docs/DECISIONS.md."""
    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        start_run._sfn_client = None
        event = _api_event(body='{"user_id": "user_demo_001", "question": "hello", "run_id": 12345}')
        result = start_run.handler(event, None)
    assert result["statusCode"] == 400


def test_start_run_non_string_question_returns_400_not_an_eventual_step_functions_failure(state_machine_arn):
    """A non-string question used to sail through to a real Step Functions
    execution and only fail later, inside agent_task's HealthAgent.ask()
    (by design there -- see agent_task.py's docstring -- but wasteful and
    less clear than rejecting it here at the API boundary)."""
    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        start_run._sfn_client = None
        event = _api_event(body='{"user_id": "user_demo_001", "question": 12345}')
        result = start_run.handler(event, None)
    assert result["statusCode"] == 400


def test_start_run_handles_execution_already_exists_with_matching_input_as_idempotent_retry():
    """Same run_id, same request, submitted twice -- moto doesn't actually
    enforce ExecutionAlreadyExists for duplicate names, so this exercises
    the except-branch directly against a mocked client instead.
    Regression test: this used to always report "RUNNING" regardless of
    the execution's real status; now it reports whatever
    describe_execution actually returns."""
    

    matching_input = json.dumps(
        {"run_id": "dup-1", "user_id": "user_demo_001", "question": "hello", "owner_sub": _DEFAULT_CALLER_SUB}
    )
    fake_client = MagicMock()
    fake_client.exceptions.ExecutionAlreadyExists = ClientError
    fake_client.start_execution.side_effect = ClientError({"Error": {"Code": "ExecutionAlreadyExists"}}, "StartExecution")
    fake_client.describe_execution.return_value = {"status": "SUCCEEDED", "input": matching_input}

    fake_arn = "arn:aws:states:us-east-1:123456789012:stateMachine:x"
    with patch("boto3.client", return_value=fake_client), patch.dict(os.environ, {"STATE_MACHINE_ARN": fake_arn}):
        start_run._sfn_client = None
        event = _api_event(body='{"user_id": "user_demo_001", "question": "hello", "run_id": "dup-1"}')
        result = start_run.handler(event, None)

    assert result["statusCode"] == 202
    assert json.loads(result["body"])["status"] == "SUCCEEDED"


def test_start_run_handles_execution_already_exists_with_different_input_as_conflict():
    """Regression test: an independent review found that ANY run_id reuse
    was treated as a harmless idempotent retry, regardless of whether the
    request's actual input matched the existing execution's -- a second,
    different request could silently piggyback on someone else's
    already-running or already-finished execution instead of being told
    about the conflict."""
    

    different_input = json.dumps(
        {"run_id": "dup-1", "user_id": "someone_else", "question": "a totally different question", "owner_sub": "other-sub"}
    )
    fake_client = MagicMock()
    fake_client.exceptions.ExecutionAlreadyExists = ClientError
    fake_client.start_execution.side_effect = ClientError({"Error": {"Code": "ExecutionAlreadyExists"}}, "StartExecution")
    fake_client.describe_execution.return_value = {"status": "RUNNING", "input": different_input}

    fake_arn = "arn:aws:states:us-east-1:123456789012:stateMachine:x"
    with patch("boto3.client", return_value=fake_client), patch.dict(os.environ, {"STATE_MACHINE_ARN": fake_arn}):
        start_run._sfn_client = None
        event = _api_event(body='{"user_id": "user_demo_001", "question": "hello", "run_id": "dup-1"}')
        result = start_run.handler(event, None)

    assert result["statusCode"] == 409


@pytest.mark.parametrize("bad_run_id", ["has spaces", "has/slash", "x" * 81, "quote\"mark"])
def test_start_run_rejects_invalid_run_id_characters(state_machine_arn, bad_run_id):
    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        start_run._sfn_client = None
        event = _api_event(body=json.dumps({"user_id": "user_demo_001", "question": "hello", "run_id": bad_run_id}))
        result = start_run.handler(event, None)
    assert result["statusCode"] == 400


# -- get_run ----------------------------------------------------------------
def test_get_run_returns_existing_item(runs_table):
    runs_table.put_item(Item={"run_id": "r1", "status": "SUCCEEDED", "answer": "hi", "owner_sub": _DEFAULT_CALLER_SUB})
    result = get_run.handler(_api_event(path_params={"run_id": "r1"}), None)
    assert result["statusCode"] == 200
    import json

    assert json.loads(result["body"])["status"] == "SUCCEEDED"


def test_get_run_missing_returns_404(runs_table):
    result = get_run.handler(_api_event(path_params={"run_id": "does-not-exist"}), None)
    assert result["statusCode"] == 404


def test_get_run_missing_path_param_returns_400(runs_table):
    result = get_run.handler(_api_event(path_params={}), None)
    assert result["statusCode"] == 400


def test_get_run_owned_by_another_caller_returns_404_not_403(runs_table):
    """Regression test: an independent review found that any authenticated
    caller could read any other caller's run by run_id alone -- there was
    no check against who actually created it. A non-owner gets the same
    404 a missing run_id gets, not a 403 or the real data: confirming
    "this exists but isn't yours" would itself leak information."""
    runs_table.put_item(Item={"run_id": "r1", "status": "SUCCEEDED", "answer": "secret", "owner_sub": _OTHER_CALLER_SUB})
    result = get_run.handler(_api_event(path_params={"run_id": "r1"}, sub=_DEFAULT_CALLER_SUB), None)
    assert result["statusCode"] == 404
    assert "secret" not in result["body"]


# -- cancel_run ---------------------------------------------------------
def test_cancel_run_wins_race_and_stops_execution(runs_table, state_machine_arn):
    execution_name = "r1"

    sfn = boto3.client("stepfunctions", region_name="us-east-1")
    sfn.start_execution(stateMachineArn=state_machine_arn, name=execution_name)
    runs_table.put_item(
        Item={"run_id": execution_name, "status": "RUNNING", "owner_sub": _DEFAULT_CALLER_SUB, "execution_type": "STEP_FUNCTIONS"}
    )

    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        cancel_run._sfn_client = None
        result = cancel_run.handler(_api_event(path_params={"run_id": execution_name}), None)

    assert result["statusCode"] == 200
    item = runs_table.get_item(Key={"run_id": execution_name})["Item"]
    assert item["status"] == "CANCELLED"


def test_cancel_run_cancels_a_queued_sqs_job_without_attempting_stop_execution(runs_table, state_machine_arn):
    """Regression test: an independent review found that stop_execution
    used to be called unconditionally, including for SQS-queued jobs that
    never had a Step Functions execution at all -- the resulting
    ExecutionDoesNotExist ClientError was silently swallowed by a bare
    `except ClientError: pass`, and success was reported anyway even
    though process_job.py would go on to overwrite the "cancelled" record
    the moment it picked the message up. Now: execution_type gates whether
    stop_execution is even attempted, and process_job.py's own conditional
    writes are what make the cancellation actually stick."""
    runs_table.put_item(Item={"run_id": "q1", "status": "QUEUED", "owner_sub": _DEFAULT_CALLER_SUB, "execution_type": "SQS"})

    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        cancel_run._sfn_client = None
        with patch("cancel_run._sfn") as mock_sfn:
            result = cancel_run.handler(_api_event(path_params={"run_id": "q1"}), None)
            mock_sfn.return_value.stop_execution.assert_not_called()

    assert result["statusCode"] == 200
    item = runs_table.get_item(Key={"run_id": "q1"})["Item"]
    assert item["status"] == "CANCELLED"


def test_cancel_run_refuses_to_cancel_a_synchronous_ask_run(runs_table):
    """Regression test: a second independent review found that cancelling
    a synchronous /ask run (execution_type=SYNC) used to be accepted (its
    condition only checked owner+status, not execution_type) and reported
    200 CANCELLED -- but adapter.py's own terminal write for that run is
    unconditional, so the moment the in-flight agent call finished, the
    CANCELLED record was silently overwritten back to SUCCEEDED/FAILED.
    There's also no execution to actually stop for a synchronous call.
    Correct behavior: refuse the cancellation outright (409), not report a
    success that won't stick."""
    runs_table.put_item(Item={"run_id": "sync-1", "status": "RUNNING", "owner_sub": _DEFAULT_CALLER_SUB, "execution_type": "SYNC"})

    with patch.dict(os.environ, {"STATE_MACHINE_ARN": "arn:aws:states:us-east-1:123456789012:stateMachine:fake"}):
        cancel_run._sfn_client = None
        result = cancel_run.handler(_api_event(path_params={"run_id": "sync-1"}), None)

    assert result["statusCode"] == 409
    item = runs_table.get_item(Key={"run_id": "sync-1"})["Item"]
    assert item["status"] == "RUNNING"


def test_cancel_run_loses_race_when_already_finalized(runs_table, state_machine_arn):
    runs_table.put_item(
        Item={
            "run_id": "r1",
            "status": "SUCCEEDED",
            "answer": "already done",
            "owner_sub": _DEFAULT_CALLER_SUB,
            "execution_type": "STEP_FUNCTIONS",
        }
    )

    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        cancel_run._sfn_client = None
        result = cancel_run.handler(_api_event(path_params={"run_id": "r1"}), None)

    assert result["statusCode"] == 409
    body_status = result["body"]
    assert "SUCCEEDED" in body_status  # reports the real status, doesn't claim cancellation


def test_cancel_run_missing_run_returns_404(runs_table, state_machine_arn):
    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        cancel_run._sfn_client = None
        result = cancel_run.handler(_api_event(path_params={"run_id": "never-existed"}), None)
    assert result["statusCode"] == 404


def test_cancel_run_missing_path_param_returns_400(runs_table, state_machine_arn):
    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        cancel_run._sfn_client = None
        result = cancel_run.handler(_api_event(path_params={}), None)
    assert result["statusCode"] == 400


def test_cancel_run_owned_by_another_caller_returns_404_not_the_real_status(runs_table, state_machine_arn):
    """Regression test: an independent review found that any authenticated
    caller could cancel any other caller's run by run_id alone. A
    non-owner gets the same 404 a missing run_id gets, and the run is left
    untouched -- not cancelled, not told the real status."""
    runs_table.put_item(
        Item={"run_id": "r1", "status": "RUNNING", "owner_sub": _OTHER_CALLER_SUB, "execution_type": "STEP_FUNCTIONS"}
    )

    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        cancel_run._sfn_client = None
        result = cancel_run.handler(_api_event(path_params={"run_id": "r1"}, sub=_DEFAULT_CALLER_SUB), None)

    assert result["statusCode"] == 404
    item = runs_table.get_item(Key={"run_id": "r1"})["Item"]
    assert item["status"] == "RUNNING"  # untouched
