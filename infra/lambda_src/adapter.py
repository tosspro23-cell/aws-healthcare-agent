"""AWS Lambda adapter: thin translation layer between an API Gateway HTTP
API event and `care_agent.HealthAgent`.

Deliberately thin. All reasoning/grounding/safety/retrieval logic lives in
`care_agent`, unchanged from how it runs locally -- this module's only job
is: parse the request, call the agent, persist a run record, serialize the
response. It must never reimplement or bypass anything `care_agent` already
does (in particular, never construct or alter the answer text here).

This is the *synchronous* path (Phase 1/2). `agent_task.py` is the async
equivalent invoked as a Step Functions Task (Phase 3) -- both share the
same `HealthAgent` construction via `agent_runtime.py`.

The DynamoDB write is a conditional *create* (`attribute_not_exists`), not
a plain overwrite -- `/ask`, `/runs` (Step Functions), and `/jobs` (SQS)
all share the same `run_id` keyspace, and a plain `put_item` here used to
silently replace whatever another path had already written for that
run_id, including erasing its `status`. See
`docs/INDEPENDENT_REVIEW_FINDINGS.md` (finding #2).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import auth_context
import boto3
from agent_runtime import agent as _agent
from botocore.exceptions import ClientError
from run_id_validation import is_valid_run_id

from care_agent.data_store import UnknownUserError

_RUNS_TABLE_NAME = os.environ.get("RUNS_TABLE_NAME")
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
        # Valid JSON (e.g. `null`, `[]`, `"a string"`) that isn't a JSON
        # object has no `.get()` -- treat it the same as malformed input
        # rather than letting it fall through to an unhandled AttributeError.
        return _json_response(400, {"error": "Request body must be a JSON object."})

    user_id = body.get("user_id")
    question = body.get("question")
    if not isinstance(user_id, str) or not user_id or not isinstance(question, str) or not question:
        # Covers both "missing" and "wrong type" (e.g. a number or list) --
        # a truthiness-only check let a non-string question_text reach
        # HealthAgent.ask() and blow up inside it (AttributeError from a
        # `.lower()` call deep in intent classification), which the
        # broad except below then turned into a 500 leaking that internal
        # exception message. Wrong input type is the caller's mistake, not
        # ours, so it belongs in this 400 branch instead.
        return _json_response(400, {"error": "Both 'user_id' and 'question' are required and must be non-empty strings."})

    run_id = body.get("run_id") or str(uuid.uuid4())
    if not isinstance(run_id, str):
        return _json_response(400, {"error": "'run_id', if supplied, must be a string."})
    if not is_valid_run_id(run_id):
        # /ask, /runs, and /jobs share this run_id keyspace; only /runs
        # actually needs it to be a valid Step Functions execution name,
        # but validating the same constraint everywhere means a run_id
        # accepted on any one path is usable on all three.
        return _json_response(400, {"error": "'run_id', if supplied, must be 1-80 characters with no whitespace or special characters."})

    owner_sub = auth_context.owner_sub_from_event(event)
    table = _dynamodb().Table(_RUNS_TABLE_NAME) if _RUNS_TABLE_NAME else None

    if table is not None:
        # Reserve the run_id *before* calling the agent -- a doomed
        # request (run_id already used by another path) fails fast here
        # instead of after an avoidable Bedrock call. `attribute_not_exists`
        # is what makes this a genuine cross-path collision guard rather
        # than a plain overwrite.
        try:
            table.put_item(
                Item={
                    "run_id": run_id,
                    "status": "RUNNING",
                    "owner_sub": owner_sub,
                    "execution_type": "SYNC",
                    "user_id": user_id,
                    "question": question,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                },
                ConditionExpression="attribute_not_exists(run_id)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return _json_response(409, {"error": f"run_id={run_id!r} is already in use by another run."})

    try:
        response = _agent.ask(user_id=user_id, question_text=question, question_id=run_id)
    except UnknownUserError:
        if table is not None:
            table.update_item(
                Key={"run_id": run_id},
                UpdateExpression="SET #status = :failed, error_message = :e, completed_at = :t",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":failed": "FAILED",
                    ":e": f"No data on file for user_id={user_id!r}.",
                    ":t": datetime.now(timezone.utc).isoformat(),
                },
            )
        return _json_response(404, {"error": f"No data on file for user_id={user_id!r}."})
    except Exception as exc:  # noqa: BLE001 -- outermost boundary: turn any
        # unexpected internal failure into a clean 500 with context, rather
        # than an opaque Lambda platform error.
        if table is not None:
            table.update_item(
                Key={"run_id": run_id},
                UpdateExpression="SET #status = :failed, error_message = :e, completed_at = :t",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":failed": "FAILED", ":e": str(exc), ":t": datetime.now(timezone.utc).isoformat()},
            )
        return _json_response(500, {"error": f"Agent execution failed: {exc}"})

    trace_dict = response.trace.as_dict()
    created_at = datetime.now(timezone.utc).isoformat()

    if table is not None:
        table.update_item(
            Key={"run_id": run_id},
            UpdateExpression="SET #status = :succeeded, answer = :a, safe = :safe, narrator_backend = :nb, completed_at = :t",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":succeeded": "SUCCEEDED",
                ":a": response.answer,
                ":safe": response.safe,
                ":nb": response.trace.narrator_backend,
                ":t": created_at,
            },
        )

    if _EVIDENCE_BUCKET_NAME:
        _s3().put_object(
            Bucket=_EVIDENCE_BUCKET_NAME,
            Key=f"{run_id}.json",
            Body=json.dumps(trace_dict, default=str).encode("utf-8"),
            ContentType="application/json",
        )

    return _json_response(
        200,
        {
            "run_id": run_id,
            "answer": response.answer,
            "safe": response.safe,
            "trace": trace_dict,
        },
    )
