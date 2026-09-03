"""Step Functions Task Lambda: the state machine's first step. Writes the
initial `RUNNING` record for a run.

Plain (unconditional) put -- Step Functions execution-name uniqueness
(`run_id` is used as the execution name, see `start_run.py`) already
prevents a genuine duplicate start under the same run_id; this step isn't
where the interesting concurrency problem lives. `record_result.py`'s
conditional write (RUNNING -> terminal) is.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import boto3

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

    _dynamodb().Table(_RUNS_TABLE_NAME).put_item(
        Item={
            "run_id": run_id,
            "status": "RUNNING",
            "user_id": user_id,
            "question": question,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    return {"run_id": run_id, "user_id": user_id, "question": question}
