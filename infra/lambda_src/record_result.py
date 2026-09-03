"""Step Functions Task Lambda: finalizes a run.

This is where the terminal-state ownership race actually lives: this
handler and `cancel_run.py` (Phase 3's other terminal writer -- a run can
be cancelled from outside the state machine at any time) both attempt the
*same* conditional write shape: `ConditionExpression: status = RUNNING`.
DynamoDB's atomic compare-and-swap guarantees exactly one of them wins for
any given run_id, regardless of timing. Losing the race here is expected,
not an error -- it means the run was already finalized (most likely
cancelled) before this state ran, and there's nothing left to do.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

_RUNS_TABLE_NAME = os.environ["RUNS_TABLE_NAME"]

_dynamodb_resource = None


def _dynamodb():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource


def handler(event: dict, context: object) -> dict:
    run_id = event["run_id"]
    outcome = event["outcome"]  # "SUCCEEDED" | "FAILED" | "TIMED_OUT"

    table = _dynamodb().Table(_RUNS_TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat()

    update_parts = ["#status = :outcome", "completed_at = :t"]
    names = {"#status": "status"}
    values: dict[str, object] = {":outcome": outcome, ":running": "RUNNING", ":t": now}

    if outcome == "SUCCEEDED":
        update_parts += ["answer = :a", "safe = :safe"]
        values[":a"] = event["answer"]
        values[":safe"] = event["safe"]
    else:
        update_parts.append("error_message = :e")
        values[":e"] = event.get("error", "unknown error")

    try:
        table.update_item(
            Key={"run_id": run_id},
            UpdateExpression="SET " + ", ".join(update_parts),
            ConditionExpression="#status = :running",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        won_race = True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        won_race = False

    return {"run_id": run_id, "outcome": outcome, "finalized_by_this_step": won_race}
