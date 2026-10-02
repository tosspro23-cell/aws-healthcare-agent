"""Shared DynamoDB+S3 read helper for cross-turn conversation grounding.

A follow-up question's `prior_run_ids` (the Workbench's own recent-turn
window -- see `frontend/src/components/Workbench.tsx`'s
`buildContextualQuestion`) names earlier runs in the *same* conversation
whose already-computed `grounded_facts` should widen this turn's own
`numeric_grounding` check, so a narrator correctly recalling an
already-verified number from an earlier turn isn't treated as inventing
it. The server re-fetches and re-authorizes each one itself here, rather
than trusting a client-supplied fact list directly -- this project's
whole "every claim traces to a source the server itself verified" stance
extends to this input channel too, not just to the narrator's own output.

Mirrors `run_writes.py`'s pattern (one flat module, imported the same way
by every handler that needs it) and `get_run.py`'s own two checks
(ownership comparison, best-effort S3 fetch that degrades to "skip, don't
error" on anything missing/inaccessible/corrupt) -- factored out here
because this is now the third caller of the same ownership-check shape
(`adapter.py`, `agent_task.py`, `process_job.py`), not duplicated a third
time.
"""

from __future__ import annotations

import json
import logging
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_RUNS_TABLE_NAME = os.environ.get("RUNS_TABLE_NAME")
_EVIDENCE_BUCKET_NAME = os.environ.get("EVIDENCE_BUCKET_NAME")

# Defensive cap, independent of whatever window the frontend actually
# sends (today `MAX_CONTEXT_TURNS` = 4 turns) -- bounds the worst case of
# a malformed or adversarial request triggering an unbounded number of
# DynamoDB/S3 round-trips in one Lambda invocation.
_MAX_PRIOR_RUN_IDS = 10

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


def fetch_prior_grounded_facts(run_ids: list[str], owner_sub: str | None) -> list[dict]:
    """For each of `run_ids` (capped at `_MAX_PRIOR_RUN_IDS`), returns that
    run's stored `grounded_facts` (raw JSON dicts, not `GroundedFact`
    objects -- reconstructing those is `care_agent`'s job; this module
    stays AWS-only) if, and only if, the run exists, belongs to
    `owner_sub`, and already has an evidence object written for it.
    Anything else for a given id -- missing record, wrong owner, run
    still in progress, corrupt/unreadable evidence -- is silently
    skipped, never raised: this is best-effort conversational context,
    not the source of truth for anything, so a caller sending a stale or
    foreign run_id should degrade to "no extra context from that one",
    not fail the whole new question.

    Returns `[]` immediately if the runs table or evidence bucket isn't
    configured, or `run_ids` is empty -- no AWS calls made in either case.
    `owner_sub` is `None` for an async execution/message started before
    this field existed (see `agent_task.py`/`process_job.py`'s own
    defaulting comments) -- every real item's `owner_sub` is a non-empty
    string, so `None` simply matches nothing, same as any other owner
    mismatch.
    """
    if not run_ids or not _RUNS_TABLE_NAME or not _EVIDENCE_BUCKET_NAME:
        return []

    table = _dynamodb().Table(_RUNS_TABLE_NAME)
    facts: list[dict] = []
    for run_id in run_ids[:_MAX_PRIOR_RUN_IDS]:
        item = table.get_item(Key={"run_id": run_id}).get("Item")
        if item is None or item.get("owner_sub") != owner_sub:
            continue
        try:
            obj = _s3().get_object(Bucket=_EVIDENCE_BUCKET_NAME, Key=f"{run_id}.json")
            trace = json.loads(obj["Body"].read())
        except (ClientError, BotoCoreError, ValueError) as exc:
            # Same best-effort posture as get_run.py's own `_fetch_trace`
            # -- a run still in progress (no evidence written yet) is the
            # common, expected case here, not a problem.
            logger.info("No evidence available for prior_run_id=%r (%s)", run_id, exc)
            continue
        facts.extend(trace.get("grounded_facts") or [])

    return facts
