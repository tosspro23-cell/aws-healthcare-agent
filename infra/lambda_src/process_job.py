"""SQS-triggered Lambda: consumes one queued job and runs the agent.

This is the queue-buffered alternative to `agent_task.py` (Step
Functions) -- same `HealthAgent.ask()` call, same shared
`agent_runtime.py` construction, same `CARE_AGENT_NARRATOR_BACKEND=bedrock`
wiring, but invoked by SQS instead of by a Step Functions Task, and with
concurrency bounded by the queue's event-source `max_concurrency` (see
`../stacks/queue_stack.py`) instead of by an explicit `add_retry` policy.

Retry semantics are deliberately queue-native here, not Step-Functions-
style: an *unknown/permanent* failure (`UnknownUserError`) is caught and
written straight to `FAILED` -- retrying it would never succeed, so
there's no point spending one of the queue's limited redelivery attempts
on it (mirrors `adapter.py`'s explicit 404 handling for the same case).
Any *other* exception (a transient Bedrock throttle, an unexpected bug)
is left to propagate: Lambda reports the invocation as failed, SQS makes
the message visible again after its visibility timeout for another
attempt, and after `maxReceiveCount` attempts (see the queue's redrive
policy) it moves to the dead-letter queue instead of retrying forever --
`reconcile_dlq.py` is what finally marks the run FAILED at that point,
since nothing else in this path ever will.

Every write below is conditional on the record's *current* status, not
an unconditional overwrite. SQS is at-least-once delivery: a redelivery of
the same message (because the first attempt's Lambda timed out, or its
ack didn't land before the visibility timeout expired), or a second,
independent message for the same run_id (a client's own retry against
`enqueue_job.py`), can trigger a second, concurrent invocation for a
run_id this handler is already processing. Entering RUNNING is guarded
by `_claim_for_processing`'s processing lease (see its own docstring) --
a genuine second concurrent invocation is rejected there and never calls
the agent at all, closing the "two deliveries both call Bedrock" gap an
independent review found still open after the plain status-only
conditional write. See `docs/INDEPENDENT_REVIEW_FINDINGS.md` (round 2,
finding #9) and `docs/DECISIONS.md`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import boto3
import run_writes
from agent_runtime import agent as _agent
from botocore.exceptions import ClientError

from care_agent.data_store import UnknownUserError

_RUNS_TABLE_NAME = os.environ["RUNS_TABLE_NAME"]
_EVIDENCE_BUCKET_NAME = os.environ.get("EVIDENCE_BUCKET_NAME")

_s3_client = None

# A few seconds beyond this handler's own Lambda timeout (30s -- see
# ../stacks/queue_stack.py) -- long enough that a still-legitimately-
# running invocation's lease can never expire out from under it (Lambda
# hard-kills at 30s, so no invocation can physically still be running
# once its lease would expire), short enough that a genuinely
# crashed/killed invocation's lease is reclaimable soon after, rather
# than waiting for the queue's own 90s visibility timeout -- a different,
# unrelated margin (see queue_stack.py's own comment on that one).
_LEASE_SECONDS = 35


def _s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def _claim_for_processing(run_id: str, *, now: str, lease_expires_at: str) -> bool:
    """Atomically claims run_id for processing by this invocation, or
    returns False if someone else already holds an unexpired claim.

    A plain "status in (QUEUED, RUNNING)" condition can't distinguish a
    legitimate redelivery of a message whose *prior* attempt already
    finished or crashed from a second delivery that's genuinely
    concurrent with a *still-running* first attempt -- both see
    status=RUNNING and would proceed to call the agent again. The
    `processing_lease_expires_at` field closes that: a RUNNING record can
    only be re-claimed once its lease has actually expired, which (given
    `_LEASE_SECONDS` above) can only happen once the prior invocation is
    provably no longer running. DynamoDB's atomic compare-and-swap
    guarantees exactly one concurrent caller ever wins this condition for
    a given run_id, regardless of timing.
    """
    table = run_writes._dynamodb().Table(_RUNS_TABLE_NAME)
    try:
        table.update_item(
            Key={"run_id": run_id},
            UpdateExpression="SET #status = :status, #started = :now, #lease = :lease_expires",
            ConditionExpression=("#status = :queued OR (#status = :status AND (attribute_not_exists(#lease) OR #lease < :now))"),
            ExpressionAttributeNames={"#status": "status", "#started": "started_at", "#lease": "processing_lease_expires_at"},
            ExpressionAttributeValues={
                ":status": "RUNNING",
                ":queued": "QUEUED",
                ":now": now,
                ":lease_expires": lease_expires_at,
            },
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return False


def handler(event: dict, context: object) -> None:
    # batch_size=1 (see queue_stack.py) -- exactly one record per invocation.
    for record in event["Records"]:
        message = json.loads(record["body"])
        run_id = message["run_id"]
        user_id = message["user_id"]
        question = message["question"]

        now = datetime.now(timezone.utc)
        entered_running = _claim_for_processing(
            run_id,
            now=now.isoformat(),
            lease_expires_at=(now + timedelta(seconds=_LEASE_SECONDS)).isoformat(),
        )
        if not entered_running:
            continue

        try:
            response = _agent.ask(user_id=user_id, question_text=question, question_id=run_id)
        except UnknownUserError as exc:
            run_writes.conditional_status_write(
                run_id,
                if_status_in=("RUNNING",),
                status="FAILED",
                error_message=str(exc),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            continue

        # Persisted to the same {run_id}.json key adapter.py/agent_task.py
        # use, so get_run.py can find a full grounding trace for this path
        # too -- previously only the synchronous /ask path had one
        # anywhere. Written before the final DynamoDB write, same
        # ordering rationale as adapter.py's own fix: if this raises, it
        # propagates per this module's existing retry philosophy (SQS
        # redelivers, the run stays RUNNING rather than being falsely
        # marked SUCCEEDED with no evidence).
        if _EVIDENCE_BUCKET_NAME:
            _s3().put_object(
                Bucket=_EVIDENCE_BUCKET_NAME,
                Key=f"{run_id}.json",
                Body=json.dumps(response.trace.as_dict(), default=str).encode("utf-8"),
                ContentType="application/json",
            )

        run_writes.conditional_status_write(
            run_id,
            if_status_in=("RUNNING",),
            status="SUCCEEDED",
            answer=response.answer,
            safe=response.safe,
            narrator_backend=response.trace.narrator_backend,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
