"""API handler for `POST /runs/{run_id}/cancel`: cancels an in-flight run.

This is the terminal-state ownership race the whole Phase 3 exercise is
about: this handler and the state machine's own `RecordSuccess`/
`RecordFailure` states (see `../stacks/orchestration_stack.py`) can race to
finalize the same run_id. Whoever wins is decided by a single atomic
DynamoDB conditional write (`ConditionExpression: status = RUNNING`) --
the AWS analog of an ETag/CAS-based optimistic-concurrency pattern.

Order matters here: the conditional DynamoDB write happens *first*, and
`StopExecution` is only called if that write actually won the race. If the
run already finished (success or failure) before this handler's write
lands, there's nothing to stop, and this handler correctly reports the
real current status instead of falsely claiming a cancellation happened.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

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

    table = _dynamodb().Table(_RUNS_TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat()

    try:
        table.update_item(
            Key={"run_id": run_id},
            UpdateExpression="SET #status = :cancelled, completed_at = :t",
            ConditionExpression="#status = :running",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":cancelled": "CANCELLED", ":running": "RUNNING", ":t": now},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        # Lost the race (or the run never existed / already finished) --
        # report the real current state rather than pretending we cancelled it.
        item = table.get_item(Key={"run_id": run_id}).get("Item")
        if item is None:
            return _json_response(404, {"error": f"No run found for run_id={run_id!r}."})
        return _json_response(
            409,
            {"run_id": run_id, "status": item.get("status"), "message": "Run was already finalized; nothing to cancel."},
        )

    # Won the race -- best-effort stop the actual execution. If it already
    # finished naturally between our DynamoDB write and this call, that's
    # fine: the DynamoDB record (the source of truth this API reads from)
    # is already correctly CANCELLED regardless of what StopExecution does.
    try:
        _sfn().stop_execution(executionArn=_execution_arn_for(run_id), cause="Cancelled via API")
    except ClientError:
        pass

    return _json_response(200, {"run_id": run_id, "status": "CANCELLED"})
