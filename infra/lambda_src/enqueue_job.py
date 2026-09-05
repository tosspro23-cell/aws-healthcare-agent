"""API handler for `POST /jobs`: enqueues an agent run onto SQS instead of
starting it directly.

This is the queue-buffered alternative to `start_run.py` (Step Functions)
and `adapter.py` (synchronous) -- see `../stacks/queue_stack.py` for why.
Deliberately lean: a `SendMessage` and one DynamoDB `put_item`, both
sub-100ms operations, so this Lambda's own concurrent-execution footprint
is tiny compared to `process_job.py` (which makes the multi-second Bedrock
call). Writes an initial `QUEUED` record so a client polling
`GET /runs/{run_id}` immediately after submission sees something
meaningful rather than a 404, using the exact same table/schema
`start_run.py`'s Step Functions path uses -- `get_run.py` is schema-agnostic
(just returns whatever's under `run_id`), so no changes were needed there
to support polling this path too.

The put is a conditional *create* (`attribute_not_exists(run_id)`), not a
plain overwrite -- `/ask`, `/runs`, and `/jobs` share the same `run_id`
keyspace, and a collision used to silently let one path's record replace
another's. `owner_sub` (the authenticated caller's Cognito `sub`) is
persisted so `get_run.py`/`cancel_run.py` can enforce that only the run's
creator may read or cancel it. See
`docs/INDEPENDENT_REVIEW_FINDINGS.md` (findings #1/#2).

The DynamoDB write and the SQS `send_message` below are still two
separate, non-atomic calls (finding #3) -- a `send_message` failure now
gets a compensating write (`FAILED`, not a silently orphaned `QUEUED`
record forever), but a Lambda crash *between* the two calls is still an
unhandled gap. Closing that fully needs a real outbox/reconciliation
pattern, not a try/except; see `docs/INDEPENDENT_REVIEW_FINDINGS.md`.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import auth_context
import boto3
from botocore.exceptions import ClientError
from run_id_validation import is_valid_run_id

_RUNS_TABLE_NAME = os.environ["RUNS_TABLE_NAME"]
_QUEUE_URL = os.environ["JOBS_QUEUE_URL"]

_dynamodb_resource = None
_sqs_client = None


def _dynamodb():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource


def _sqs():
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = boto3.client("sqs")
    return _sqs_client


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
        return _json_response(400, {"error": "'run_id', if supplied, must be a string."})
    if not is_valid_run_id(run_id):
        return _json_response(400, {"error": "'run_id', if supplied, must be 1-80 characters with no whitespace or special characters."})

    owner_sub = auth_context.owner_sub_from_event(event)
    table = _dynamodb().Table(_RUNS_TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat()
    try:
        table.put_item(
            Item={
                "run_id": run_id,
                "status": "QUEUED",
                "owner_sub": owner_sub,
                "execution_type": "SQS",
                "user_id": user_id,
                "question": question,
                "queued_at": now,
            },
            ConditionExpression="attribute_not_exists(run_id)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return _json_response(409, {"error": f"run_id={run_id!r} is already in use by another run."})

    try:
        _sqs().send_message(
            QueueUrl=_QUEUE_URL,
            MessageBody=json.dumps({"run_id": run_id, "user_id": user_id, "question": question}),
        )
    except Exception as exc:  # noqa: BLE001 -- compensating write below, then report failure
        # The DynamoDB record above and this send are two separate calls,
        # not one transaction. Without this, a send failure here used to
        # leave the record `QUEUED` forever with no message ever coming --
        # an independent review caught this (finding #3). This doesn't
        # make the pair atomic (a Lambda crash between the two calls still
        # isn't covered -- that needs a real outbox/reconciliation
        # pattern, not a try/except), but it closes the common case: a
        # `send_message` call that fails synchronously no longer leaves a
        # silently orphaned record.
        table.update_item(
            Key={"run_id": run_id},
            UpdateExpression="SET #status = :failed, error_message = :e, completed_at = :t",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":failed": "FAILED",
                ":e": f"Failed to enqueue job: {exc}",
                ":t": datetime.now(timezone.utc).isoformat(),
            },
        )
        return _json_response(500, {"error": f"Failed to enqueue job: {exc}"})

    return _json_response(202, {"run_id": run_id, "status": "QUEUED"})
