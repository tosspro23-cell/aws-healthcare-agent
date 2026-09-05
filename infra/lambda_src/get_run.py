"""API handler for `GET /runs/{run_id}`: reads current run status/result.

Read-only -- just fetches the item the state machine (or the cancel
handler) has written to DynamoDB. See `../stacks/orchestration_stack.py`
for who writes `status` and when.

Enforces that only the run's creator (`owner_sub`, the JWT `sub` that
created it -- see `auth_context.py`) may read it. Before this check
existed, any authenticated caller who knew or guessed a `run_id` could
read any other caller's run, including its full answer text -- see
`docs/INDEPENDENT_REVIEW_FINDINGS.md` (finding #1). A non-owner gets the
same 404 as a genuinely missing run_id, not a 403 -- confirming "this
exists but isn't yours" would itself leak information to a caller who
shouldn't be able to tell the difference.
"""

from __future__ import annotations

import json
import os
from typing import Any

import auth_context
import boto3

_RUNS_TABLE_NAME = os.environ["RUNS_TABLE_NAME"]

_dynamodb_resource = None


def _dynamodb():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource


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

    item = _dynamodb().Table(_RUNS_TABLE_NAME).get_item(Key={"run_id": run_id}).get("Item")
    owner_sub = auth_context.owner_sub_from_event(event)
    if item is None or item.get("owner_sub") != owner_sub:
        return _json_response(404, {"error": f"No run found for run_id={run_id!r}."})

    return _json_response(200, dict(item))
