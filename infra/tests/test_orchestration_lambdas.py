"""Tests for the Phase 3 Lambda handlers (mark_running, agent_task,
record_result, start_run, get_run, cancel_run) -- all against moto-mocked
DynamoDB/Step Functions, no real AWS account, no network call.
"""

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
    result = mark_running.handler({"run_id": "r1", "user_id": "user_demo_001", "question": "hi"}, None)
    assert result == {"run_id": "r1", "user_id": "user_demo_001", "question": "hi"}
    item = runs_table.get_item(Key={"run_id": "r1"})["Item"]
    assert item["status"] == "RUNNING"
    assert item["user_id"] == "user_demo_001"
    assert "started_at" in item


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
    result = record_result.handler({"run_id": "r1", "outcome": "SUCCEEDED", "answer": "the answer", "safe": True}, None)
    assert result["finalized_by_this_step"] is True
    item = runs_table.get_item(Key={"run_id": "r1"})["Item"]
    assert item["status"] == "SUCCEEDED"
    assert item["answer"] == "the answer"
    assert item["safe"] is True


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
    result = record_result.handler({"run_id": "r1", "outcome": "SUCCEEDED", "answer": "too late", "safe": True}, None)
    assert result["finalized_by_this_step"] is False
    item = runs_table.get_item(Key={"run_id": "r1"})["Item"]
    assert item["status"] == "CANCELLED"  # unchanged -- the winner's write stands


# -- start_run --------------------------------------------------------------
def test_start_run_starts_execution_and_returns_202(state_machine_arn):
    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        start_run._sfn_client = None  # force a fresh client bound to the mocked region
        event = {"body": '{"user_id": "user_demo_001", "question": "hello"}'}
        result = start_run.handler(event, None)
    assert result["statusCode"] == 202
    import json

    body = json.loads(result["body"])
    assert body["status"] == "RUNNING"
    assert body["run_id"]


def test_start_run_missing_fields_returns_400(state_machine_arn):
    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        start_run._sfn_client = None
        result = start_run.handler({"body": '{"question": "hello"}'}, None)
    assert result["statusCode"] == 400


def test_start_run_non_string_run_id_returns_400_not_500(state_machine_arn):
    """Regression test: a non-string run_id used to reach `start_execution`
    unvalidated -- `name` must be a string, so this raised an uncaught
    boto3 ClientError (the only caught exception was
    ExecutionAlreadyExists) instead of a clean 400. Found via the Phase 4
    stress-test sweep, fixed in start_run.py -- see docs/DECISIONS.md."""
    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        start_run._sfn_client = None
        event = {"body": '{"user_id": "user_demo_001", "question": "hello", "run_id": 12345}'}
        result = start_run.handler(event, None)
    assert result["statusCode"] == 400


def test_start_run_non_string_question_returns_400_not_an_eventual_step_functions_failure(state_machine_arn):
    """A non-string question used to sail through to a real Step Functions
    execution and only fail later, inside agent_task's HealthAgent.ask()
    (by design there -- see agent_task.py's docstring -- but wasteful and
    less clear than rejecting it here at the API boundary)."""
    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        start_run._sfn_client = None
        event = {"body": '{"user_id": "user_demo_001", "question": 12345}'}
        result = start_run.handler(event, None)
    assert result["statusCode"] == 400


def test_start_run_handles_execution_already_exists_gracefully():
    """Same run_id submitted twice -- moto doesn't actually enforce
    ExecutionAlreadyExists for duplicate names, so this exercises the
    except-branch directly against a mocked client instead."""
    fake_client = MagicMock()
    fake_client.exceptions.ExecutionAlreadyExists = ClientError
    fake_client.start_execution.side_effect = ClientError({"Error": {"Code": "ExecutionAlreadyExists"}}, "StartExecution")

    fake_arn = "arn:aws:states:us-east-1:123456789012:stateMachine:x"
    with patch("boto3.client", return_value=fake_client), patch.dict(os.environ, {"STATE_MACHINE_ARN": fake_arn}):
        start_run._sfn_client = None
        event = {"body": '{"user_id": "user_demo_001", "question": "hello", "run_id": "dup-1"}'}
        result = start_run.handler(event, None)

    assert result["statusCode"] == 202


# -- get_run ----------------------------------------------------------------
def test_get_run_returns_existing_item(runs_table):
    runs_table.put_item(Item={"run_id": "r1", "status": "SUCCEEDED", "answer": "hi"})
    result = get_run.handler({"pathParameters": {"run_id": "r1"}}, None)
    assert result["statusCode"] == 200
    import json

    assert json.loads(result["body"])["status"] == "SUCCEEDED"


def test_get_run_missing_returns_404(runs_table):
    result = get_run.handler({"pathParameters": {"run_id": "does-not-exist"}}, None)
    assert result["statusCode"] == 404


def test_get_run_missing_path_param_returns_400(runs_table):
    result = get_run.handler({"pathParameters": {}}, None)
    assert result["statusCode"] == 400


# -- cancel_run ---------------------------------------------------------
def test_cancel_run_wins_race_and_stops_execution(runs_table, state_machine_arn):
    execution_name = "r1"

    sfn = boto3.client("stepfunctions", region_name="us-east-1")
    sfn.start_execution(stateMachineArn=state_machine_arn, name=execution_name)
    runs_table.put_item(Item={"run_id": execution_name, "status": "RUNNING"})

    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        cancel_run._sfn_client = None
        result = cancel_run.handler({"pathParameters": {"run_id": execution_name}}, None)

    assert result["statusCode"] == 200
    item = runs_table.get_item(Key={"run_id": execution_name})["Item"]
    assert item["status"] == "CANCELLED"


def test_cancel_run_loses_race_when_already_finalized(runs_table, state_machine_arn):
    runs_table.put_item(Item={"run_id": "r1", "status": "SUCCEEDED", "answer": "already done"})

    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        cancel_run._sfn_client = None
        result = cancel_run.handler({"pathParameters": {"run_id": "r1"}}, None)

    assert result["statusCode"] == 409
    body_status = result["body"]
    assert "SUCCEEDED" in body_status  # reports the real status, doesn't claim cancellation


def test_cancel_run_missing_run_returns_404(runs_table, state_machine_arn):
    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        cancel_run._sfn_client = None
        result = cancel_run.handler({"pathParameters": {"run_id": "never-existed"}}, None)
    assert result["statusCode"] == 404


def test_cancel_run_missing_path_param_returns_400(runs_table, state_machine_arn):
    with patch.dict(os.environ, {"STATE_MACHINE_ARN": state_machine_arn}):
        cancel_run._sfn_client = None
        result = cancel_run.handler({"pathParameters": {}}, None)
    assert result["statusCode"] == 400
