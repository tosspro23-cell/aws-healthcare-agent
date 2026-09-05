"""API handler for `POST /runs/{run_id}/cancel`: cancels an in-flight run.

This is the terminal-state ownership race the whole Phase 3 exercise is
about: this handler and the state machine's own `RecordSuccess`/
`RecordFailure` states (see `../stacks/orchestration_stack.py`), or
`process_job.py`'s own writes for the SQS path, can race to finalize the
same run_id. Whoever wins is decided by a single atomic DynamoDB
conditional write -- the AWS analog of an ETag/CAS-based
optimistic-concurrency pattern.

Two things this now enforces that it didn't before (see
`docs/INDEPENDENT_REVIEW_FINDINGS.md`, finding #1):

- **Ownership**: the caller's Cognito `sub` must match the run's
  `owner_sub`, checked *inside the same atomic conditional update* as the
  status check -- not as a separate get-then-act, which would leave a
  race window between the check and the write. Any authenticated caller
  used to be able to cancel any other caller's run by `run_id` alone.
- **Execution-type awareness**: `stop_execution` is only attempted for a
  Step-Functions-orchestrated run (`execution_type == "STEP_FUNCTIONS"`).
  It used to be called unconditionally, including for SQS-queued jobs
  that were never a Step Functions execution at all, raising
  `ExecutionDoesNotExist` -- caught by a bare `except ClientError: pass`
  that silently swallowed it and still reported success, even though
  `process_job.py` would go on to overwrite the "cancelled" record the
  moment it picked the message up. `process_job.py`'s own conditional
  writes (only enter `RUNNING` from `QUEUED`/`RUNNING`, only finalize from
  `RUNNING`) are what actually make cancelling an SQS-queued job stick now
  -- this handler just needs to not misreport what happened.

Order still matters: the conditional DynamoDB write happens *first*, and
`stop_execution` is only called if that write actually won the race and
the run was Step-Functions-orchestrated. If the run already finished
(success or failure) before this handler's write lands, there's nothing
to stop, and this handler correctly reports the real current status
instead of falsely claiming a cancellation happened.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import auth_context
import boto3
from botocore.exceptions import ClientError

_STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
_RUNS_TABLE_NAME = os.environ["RUNS_TABLE_NAME"]

_dynamodb_resource = None
_sfn_client = None


def _dynamodb():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource


def _sfn():
    global _sfn_client
    if _sfn_client is None:
        _sfn_client = boto3.client("stepfunctions")
    return _sfn_client


def _execution_arn_for(run_id: str) -> str:
    # arn:aws:states:{region}:{account}:stateMachine:{name}
    #   -> arn:aws:states:{region}:{account}:execution:{name}:{run_id}
    parts = _STATE_MACHINE_ARN.split(":")
    parts[5] = "execution"
    return ":".join(parts) + f":{run_id}"


def _json_response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, default=str),
    }


def handler(event: dict, context: object) -> dict:
    path_params = event.get("pathParameters") or {}
    run_id = path_params.get("run_id")
    if not run_id:
        return _json_response(400, {"error": "run_id path parameter is required."})

    owner_sub = auth_context.owner_sub_from_event(event)
    table = _dynamodb().Table(_RUNS_TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat()

    try:
        result = table.update_item(
            Key={"run_id": run_id},
            UpdateExpression="SET #status = :cancelled, completed_at = :t",
            ConditionExpression="owner_sub = :owner_sub AND (#status = :queued OR #status = :running)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":cancelled": "CANCELLED",
                ":queued": "QUEUED",
                ":running": "RUNNING",
                ":owner_sub": owner_sub,
                ":t": now,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        # Lost the race, the run never existed, already finished, or
        # belongs to someone else. A non-owner gets the same 404 as a
        # missing run_id -- confirming "this exists but isn't yours" would
        # itself leak information a non-owner shouldn't be able to learn.
        item = table.get_item(Key={"run_id": run_id}).get("Item")
        if item is None or item.get("owner_sub") != owner_sub:
            return _json_response(404, {"error": f"No run found for run_id={run_id!r}."})
        return _json_response(
            409,
            {"run_id": run_id, "status": item.get("status"), "message": "Run was already finalized; nothing to cancel."},
        )

    # Won the race -- best-effort stop the actual execution, but only if
    # this run ever *was* a Step Functions execution. An SQS-queued job
    # has no execution to stop; process_job.py's own conditional writes
    # (see its module docstring) are what make the CANCELLED status
    # written above actually stick against a job already in flight.
    if result["Attributes"].get("execution_type") == "STEP_FUNCTIONS":
        try:
            _sfn().stop_execution(executionArn=_execution_arn_for(run_id), cause="Cancelled via API")
        except ClientError:
            # Best-effort: if it already finished naturally between our
            # DynamoDB write and this call, that's fine -- the DynamoDB
            # record (the source of truth this API reads from) is already
            # correctly CANCELLED regardless of what StopExecution does.
            pass

    return _json_response(200, {"run_id": run_id, "status": "CANCELLED"})
