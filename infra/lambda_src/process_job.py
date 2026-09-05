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

Both writes below are now conditional on the record's *current* status,
not unconditional overwrites. SQS is at-least-once delivery: a redelivery
of the same message (because the first attempt's Lambda timed out, or its
ack didn't land before the visibility timeout expired) used to blindly
call the agent again and could set a record that had already reached
`SUCCEEDED` back to `RUNNING`. Worse, `cancel_run.py` writing `CANCELLED`
to this same run_id (a caller cancelling a queued/in-flight job) used to
get silently overwritten the moment this handler's own write landed,
since neither side checked the other. See
`docs/INDEPENDENT_REVIEW_FINDINGS.md` (finding #2) for the reproduction.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import boto3
from agent_runtime import agent as _agent
from botocore.exceptions import ClientError

from care_agent.data_store import UnknownUserError

_RUNS_TABLE_NAME = os.environ["RUNS_TABLE_NAME"]
_EVIDENCE_BUCKET_NAME = os.environ.get("EVIDENCE_BUCKET_NAME")

_dynamodb_resource = None
_s3_client = None


def _dynamodb():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource


def _s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def _write_result(run_id: str, *, if_status_in: tuple[str, ...] | None = None, **fields: object) -> bool:
    """Returns True if the write happened. Returns False (without raising)
    if `if_status_in` was given and the record's current status wasn't one
    of those values -- a stale/duplicate delivery lost a race against
    something else that already changed the record; that's expected, not
    an error, so the caller should treat it as "nothing more to do here,"
    not retry or propagate.

    `status` is a DynamoDB reserved keyword -- an UpdateExpression can't
    use it bare (confirmed by moto raising the exact real
    ValidationException DynamoDB itself would). Every field name gets
    aliased via ExpressionAttributeNames unconditionally, not just the
    ones known today to be reserved, so adding a future field can't
    silently reintroduce this same bug for some other reserved word.
    """
    table = _dynamodb().Table(_RUNS_TABLE_NAME)
    update_parts = [f"#{key} = :{key}" for key in fields]
    names = {f"#{key}": key for key in fields}
    values = {f":{key}": value for key, value in fields.items()}

    kwargs: dict[str, object] = {
        "Key": {"run_id": run_id},
        "UpdateExpression": "SET " + ", ".join(update_parts),
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
    }
    if if_status_in is not None:
        names["#status"] = "status"
        placeholders = [f":cond_status_{i}" for i in range(len(if_status_in))]
        kwargs["ConditionExpression"] = "#status IN (" + ", ".join(placeholders) + ")"
        for placeholder, allowed_value in zip(placeholders, if_status_in, strict=True):
            values[placeholder] = allowed_value

    try:
        table.update_item(**kwargs)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return False


def handler(event: dict, context: object) -> None:
    # batch_size=1 (see queue_stack.py) -- exactly one record per invocation.
    for record in event["Records"]:
        message = json.loads(record["body"])
        run_id = message["run_id"]
        user_id = message["user_id"]
        question = message["question"]

        now = datetime.now(timezone.utc).isoformat()
        # Allow QUEUED->RUNNING (normal) and RUNNING->RUNNING (an SQS
        # redelivery of a message still legitimately in flight) but refuse
        # to reopen a run that's already reached a terminal state --
        # most notably, one `cancel_run.py` already marked CANCELLED.
        entered_running = _write_result(run_id, if_status_in=("QUEUED", "RUNNING"), status="RUNNING", started_at=now)
        if not entered_running:
            continue

        try:
            response = _agent.ask(user_id=user_id, question_text=question, question_id=run_id)
        except UnknownUserError as exc:
            _write_result(
                run_id,
                if_status_in=("RUNNING",),
                status="FAILED",
                error_message=str(exc),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            continue

        # Persisted to the same {run_id}.json key adapter.py/agent_task.py
        # use, so get_run.py can find a full grounding trace for this path
        # too -- previously only the synchronous /ask path had one
        # anywhere. Written before the final DynamoDB write, same
        # ordering rationale as adapter.py's own fix: if this raises, it
        # propagates per this module's existing retry philosophy (SQS
        # redelivers, the run stays RUNNING rather than being falsely
        # marked SUCCEEDED with no evidence).
        if _EVIDENCE_BUCKET_NAME:
            _s3().put_object(
                Bucket=_EVIDENCE_BUCKET_NAME,
                Key=f"{run_id}.json",
                Body=json.dumps(response.trace.as_dict(), default=str).encode("utf-8"),
                ContentType="application/json",
            )

        _write_result(
            run_id,
            if_status_in=("RUNNING",),
            status="SUCCEEDED",
            answer=response.answer,
            safe=response.safe,
            narrator_backend=response.trace.narrator_backend,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
