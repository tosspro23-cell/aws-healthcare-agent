"""Step Functions Task Lambda: the state machine's first step. Writes the
initial `RUNNING` record for a run.

The put is now a conditional *create* (`attribute_not_exists(run_id)`),
not a plain overwrite. Step Functions execution-name uniqueness (`run_id`
is used as the execution name, see `start_run.py`) prevents a duplicate
Step Functions execution under the same run_id, but `/ask` and `/jobs`
(SQS) share this same DynamoDB `run_id` keyspace and know nothing about
Step Functions' own idempotency guarantee -- without this condition, a
run_id collision with either of those paths would silently overwrite (or
be overwritten by) this record. See
`docs/INDEPENDENT_REVIEW_FINDINGS.md` (finding #2). A collision here
raises `ConditionalCheckFailedException`, which is *not* in
`orchestration_stack.py`'s retry error list (retrying a genuine caller
mistake wouldn't help) and so correctly falls through to `RecordFailure`
via the state machine's catch-all.

`owner_sub` (the authenticated caller's Cognito `sub`, threaded through
from `start_run.py`'s Step Functions input) is persisted here so
`get_run.py`/`cancel_run.py` can enforce that only the run's creator may
read or cancel it -- see `docs/DECISIONS.md` for the authorization gap
this closes.
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
    user_id = event["user_id"]
    question = event["question"]
    owner_sub = event["owner_sub"]

    try:
        _dynamodb().Table(_RUNS_TABLE_NAME).put_item(
            Item={
                "run_id": run_id,
                "status": "RUNNING",
                "owner_sub": owner_sub,
                "execution_type": "STEP_FUNCTIONS",
                "user_id": user_id,
                "question": question,
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
            ConditionExpression="attribute_not_exists(run_id)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        raise RuntimeError(f"run_id={run_id!r} is already in use by another run.") from exc

    return {"run_id": run_id, "user_id": user_id, "question": question}
