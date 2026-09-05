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

On `ExecutionAlreadyExists`, this compares the *input* of the existing
execution against what this request would have submitted, and reports the
execution's *real* current status -- not a hardcoded "RUNNING". Both of
these used to be wrong: any `run_id` reuse was treated as a harmless
retry regardless of whether the input actually matched (so a second,
different request silently piggybacked on the first one's already-running
or already-finished execution instead of being told about the conflict),
and the response always claimed "RUNNING" even for an execution that had
already finished. AWS's own `StartExecution` API distinguishes exactly
this: matching input against a still-running execution is idempotent;
anything else is a real conflict. See docs/INDEPENDENT_REVIEW_FINDINGS.md
(finding #13).
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import auth_context
import boto3
from run_id_validation import is_valid_run_id

_STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]

_sfn_client = None


def _sfn():
    global _sfn_client
    if _sfn_client is None:
        _sfn_client = boto3.client("stepfunctions")
    return _sfn_client


def _execution_arn_for(run_id: str) -> str:
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
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _json_response(400, {"error": "Request body must be valid JSON."})

    if not isinstance(body, dict):
        return _json_response(400, {"error": "Request body must be a JSON object."})

    user_id = body.get("user_id")
    question = body.get("question")
    if not isinstance(user_id, str) or not user_id or not isinstance(question, str) or not question:
        return _json_response(400, {"error": "Both 'user_id' and 'question' are required and must be non-empty strings."})

    run_id = body.get("run_id") or str(uuid.uuid4())
    if not isinstance(run_id, str):
        # `start_execution`'s `name` param must be a string; an
        # unvalidated non-string run_id would otherwise raise an uncaught
        # boto3 ClientError below, surfacing as a raw Lambda platform
        # error instead of a clean 400 -- the caller's mistake, not ours.
        return _json_response(400, {"error": "'run_id', if supplied, must be a string."})
    if not is_valid_run_id(run_id):
        return _json_response(400, {"error": "'run_id', if supplied, must be 1-80 characters with no whitespace or special characters."})

    owner_sub = auth_context.owner_sub_from_event(event)
    submitted_input = {"run_id": run_id, "user_id": user_id, "question": question, "owner_sub": owner_sub}

    try:
        _sfn().start_execution(
            stateMachineArn=_STATE_MACHINE_ARN,
            name=run_id,
            input=json.dumps(submitted_input),
        )
        return _json_response(202, {"run_id": run_id, "status": "RUNNING"})
    except _sfn().exceptions.ExecutionAlreadyExists:
        pass

    # Same run_id submitted before -- find out whether this is a genuine
    # idempotent retry (same input, matching AWS's own definition) or a
    # real conflict (a different request reusing someone else's run_id),
    # and report the execution's actual current status either way.
    existing = _sfn().describe_execution(executionArn=_execution_arn_for(run_id))
    existing_input = json.loads(existing["input"])
    if existing_input != submitted_input:
        return _json_response(
            409, {"error": f"run_id={run_id!r} is already in use by a different request.", "status": existing["status"]}
        )
    return _json_response(202, {"run_id": run_id, "status": existing["status"]})
