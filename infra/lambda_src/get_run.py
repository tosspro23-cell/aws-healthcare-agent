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

Also opportunistically merges in the full grounding trace from S3, under
a `trace` key, if one has been written for this `run_id` --
`adapter.py`/`agent_task.py`/`process_job.py` all persist one to the same
`{run_id}.json` key once their run completes. A run still in progress
(no object written yet) or one from before this evidence write existed
simply has no `trace` key; that's expected, not an error.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import auth_context
import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_RUNS_TABLE_NAME = os.environ["RUNS_TABLE_NAME"]
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


def _fetch_trace(run_id: str) -> dict | None:
    if not _EVIDENCE_BUCKET_NAME:
        return None
    try:
        obj = _s3().get_object(Bucket=_EVIDENCE_BUCKET_NAME, Key=f"{run_id}.json")
        return json.loads(obj["Body"].read())
    except (ClientError, BotoCoreError, ValueError) as exc:
        # Real, reproduced-live behavior, not a hypothetical: this
        # handler is deliberately granted only s3:GetObject, not
        # s3:ListBucket (see orchestration_stack.py -- ListBucket would
        # let it enumerate every run_id's evidence in the bucket, a much
        # bigger permission than "read one object I already know the key
        # for"). Without ListBucket, S3 can't tell the caller whether a
        # missing key doesn't exist or is merely inaccessible, so it
        # returns AccessDenied instead of NoSuchKey/404 -- confirmed via
        # a real polling run that started before agent_task.py had
        # written its evidence yet. Treated the same as "no trace yet":
        # this is a best-effort enrichment on top of the DynamoDB record
        # (which already has the real status/answer), not the source of
        # truth, so any failure to fetch it should degrade gracefully
        # rather than fail the whole GET /runs/{run_id} response.
        #
        # A second independent review found the original version of this
        # fix only protected the `get_object()` call itself -- reading the
        # response stream (which can raise a transport-level error like
        # `ReadTimeoutError`, a `BotoCoreError` subclass, not a
        # `ClientError`) and parsing its JSON (which raises `ValueError`
        # on anything corrupt/truncated) both happened *outside* the try
        # block, so either would still 500 the whole endpoint. All three
        # failure modes are the same kind of problem for this specific,
        # best-effort read -- caught together, not just the one that was
        # first observed live.
        #
        # Logged, not silent: the expected case (evidence not written
        # yet) and a genuine misconfiguration both surface the same way,
        # and swallowing this with no trace at all would make a real
        # future problem invisible in CloudWatch. INFO, not ERROR/WARNING,
        # because the expected case is the common one.
        logger.info("No trace available for run_id=%r (%s)", run_id, exc)
        return None


def handler(event: dict, context: object) -> dict:
    path_params = event.get("pathParameters") or {}
    run_id = path_params.get("run_id")
    if not run_id:
        return _json_response(400, {"error": "run_id path parameter is required."})

    item = _dynamodb().Table(_RUNS_TABLE_NAME).get_item(Key={"run_id": run_id}).get("Item")
    owner_sub = auth_context.owner_sub_from_event(event)
    if item is None or item.get("owner_sub") != owner_sub:
        return _json_response(404, {"error": f"No run found for run_id={run_id!r}."})

    result = dict(item)
    trace = _fetch_trace(run_id)
    if trace is not None:
        result["trace"] = trace

    return _json_response(200, result)
