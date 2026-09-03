"""API handler for `POST /runs`: starts an async agent run.

Unlike `adapter.py` (`/ask`, synchronous), this kicks off a Step Functions
execution and returns immediately with `{"run_id": ..., "status": "RUNNING"}`
(202 Accepted) -- the caller polls `GET /runs/{run_id}` for the result.

The execution name is set to `run_id`: Step Functions treats a duplicate
execution name (for a STANDARD state machine, within the ~90-day retention
window) as `ExecutionAlreadyExists` rather than starting a second run --
a free idempotency property for "the same run_id submitted twice" on top
of the DynamoDB conditional-write terminal-state protection the state
machine itself does (see `../stacks/orchestration_stack.py`).
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import boto3

_STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]

_sfn_client = None


def _sfn():
    global _sfn_client
    if _sfn_client is None:
        _sfn_client = boto3.client("stepfunctions")
    return _sfn_client


def _json_response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, default=str),
    }


def handler(event: dict, context: object) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _json_response(400, {"error": "Request body must be valid JSON."})

    if not isinstance(body, dict):
        return _json_response(400, {"error": "Request body must be a JSON object."})

    user_id = body.get("user_id")
    question = body.get("question")
    if not user_id or not question:
        return _json_response(400, {"error": "Both 'user_id' and 'question' are required."})

    run_id = body.get("run_id") or str(uuid.uuid4())

    try:
        _sfn().start_execution(
            stateMachineArn=_STATE_MACHINE_ARN,
            name=run_id,
            input=json.dumps({"run_id": run_id, "user_id": user_id, "question": question}),
        )
    except _sfn().exceptions.ExecutionAlreadyExists:
        # Same run_id submitted twice -- not an error, just point the
        # caller at the (already in-flight or finished) existing run.
        pass

    return _json_response(202, {"run_id": run_id, "status": "RUNNING"})
