"""SQS-triggered Lambda: consumes one queued job and runs the agent.

This is the queue-buffered alternative to `agent_task.py` (Step
Functions) -- same `HealthAgent.ask()` call, same shared
`agent_runtime.py` construction, same `CARE_AGENT_NARRATOR_BACKEND=bedrock`
wiring, but invoked by SQS instead of by a Step Functions Task, and with
concurrency bounded by the queue's event-source `max_concurrency` (see
`../stacks/queue_stack.py`) instead of by an explicit `add_retry` policy.

Retry semantics are deliberately queue-native here, not Step-Functions-
style: an *unknown/permanent* failure (`UnknownUserError`) is caught and
written straight to `FAILED` -- retrying it would never succeed, so
there's no point spending one of the queue's limited redelivery attempts
on it (mirrors `adapter.py`'s explicit 404 handling for the same case).
Any *other* exception (a transient Bedrock throttle, an unexpected bug)
is left to propagate: Lambda reports the invocation as failed, SQS makes
the message visible again after its visibility timeout for another
attempt, and after `maxReceiveCount` attempts (see the queue's redrive
policy) it moves to the dead-letter queue instead of retrying forever.
This is coarser-grained than Step Functions' typed `add_catch(errors=...)`
-- SQS has no equivalent of "retry only this specific error type" -- so
the UnknownUserError split above is this handler's own way of getting
that same distinction back.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import boto3
from agent_runtime import agent as _agent

from care_agent.data_store import UnknownUserError

_RUNS_TABLE_NAME = os.environ["RUNS_TABLE_NAME"]

_dynamodb_resource = None


def _dynamodb():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource


def _write_result(run_id: str, **fields: object) -> None:
    # `status` is a DynamoDB reserved keyword -- an UpdateExpression can't
    # use it bare (confirmed by moto raising the exact real
    # ValidationException DynamoDB itself would). Every field name gets
    # aliased via ExpressionAttributeNames unconditionally, not just the
    # ones known today to be reserved, so adding a future field can't
    # silently reintroduce this same bug for some other reserved word.
    table = _dynamodb().Table(_RUNS_TABLE_NAME)
    update_parts = [f"#{key} = :{key}" for key in fields]
    names = {f"#{key}": key for key in fields}
    values = {f":{key}": value for key, value in fields.items()}
    table.update_item(
        Key={"run_id": run_id},
        UpdateExpression="SET " + ", ".join(update_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def handler(event: dict, context: object) -> None:
    # batch_size=1 (see queue_stack.py) -- exactly one record per invocation.
    for record in event["Records"]:
        message = json.loads(record["body"])
        run_id = message["run_id"]
        user_id = message["user_id"]
        question = message["question"]

        now = datetime.now(timezone.utc).isoformat()
        _write_result(run_id, status="RUNNING", started_at=now)

        try:
            response = _agent.ask(user_id=user_id, question_text=question, question_id=run_id)
        except UnknownUserError as exc:
            _write_result(
                run_id,
                status="FAILED",
                error_message=str(exc),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            continue

        _write_result(
            run_id,
            status="SUCCEEDED",
            answer=response.answer,
            safe=response.safe,
            narrator_backend=response.trace.narrator_backend,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
