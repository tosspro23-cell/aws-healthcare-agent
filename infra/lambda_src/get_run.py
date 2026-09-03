"""API handler for `GET /runs/{run_id}`: reads current run status/result.

Read-only -- just fetches the item the state machine (or the cancel
handler) has written to DynamoDB. See `../stacks/orchestration_stack.py`
for who writes `status` and when.
"""

from __future__ import annotations

import json
import os
from typing import Any

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
    if item is None:
        return _json_response(404, {"error": f"No run found for run_id={run_id!r}."})

    return _json_response(200, dict(item))
