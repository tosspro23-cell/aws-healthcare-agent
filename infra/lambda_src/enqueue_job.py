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
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3

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

    now = datetime.now(timezone.utc).isoformat()
    _dynamodb().Table(_RUNS_TABLE_NAME).put_item(
        Item={"run_id": run_id, "status": "QUEUED", "user_id": user_id, "question": question, "queued_at": now}
    )

    _sqs().send_message(
        QueueUrl=_QUEUE_URL,
        MessageBody=json.dumps({"run_id": run_id, "user_id": user_id, "question": question}),
    )

    return _json_response(202, {"run_id": run_id, "status": "QUEUED"})
