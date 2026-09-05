"""Step Functions Task Lambda for the async orchestration path (Phase 3).

Input shape (from the state machine): {"run_id": ..., "user_id": ...,
"question": ...}. Returns {"answer": ..., "safe": ..., "trace": {...}} on
success.

Deliberately lets exceptions propagate rather than catching them into a
JSON error response the way `adapter.py` does for its synchronous HTTP
path. Here, the *state machine* is the error-handling boundary: an
unhandled exception becomes a Lambda-reported task failure, which is what
the state machine's Retry/Catch blocks are designed to see and act on (see
`../stacks/orchestration_stack.py`). If this handler swallowed errors into
a 200-shaped response the way an HTTP handler would, Step Functions would
never know a failure happened and the whole point of Phase 3 -- native
retry/timeout/catch semantics -- would be silently defeated.

Also persists the full trace to S3 under the same `{run_id}.json` key
`adapter.py`'s synchronous path already uses -- until this, only the
sync path had a full grounding trace anywhere; `GET /runs/{run_id}`
(`get_run.py`) could only ever show `answer`/`safe`/`narrator_backend`
from DynamoDB for this path. Written here (the Lambda that actually has
the trace), not threaded through Step Functions state, which has its own
payload-size limits and no reason to carry a full trace through every
transition just to hand it to one reader.
"""

from __future__ import annotations

import json
import os

import boto3
from agent_runtime import agent as _agent

_EVIDENCE_BUCKET_NAME = os.environ.get("EVIDENCE_BUCKET_NAME")

_s3_client = None


def _s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def handler(event: dict, context: object) -> dict:
    run_id = event["run_id"]
    user_id = event["user_id"]
    question = event["question"]

    response = _agent.ask(user_id=user_id, question_text=question, question_id=run_id)
    trace_dict = response.trace.as_dict()

    if _EVIDENCE_BUCKET_NAME:
        _s3().put_object(
            Bucket=_EVIDENCE_BUCKET_NAME,
            Key=f"{run_id}.json",
            Body=json.dumps(trace_dict, default=str).encode("utf-8"),
            ContentType="application/json",
        )

    return {
        "answer": response.answer,
        "safe": response.safe,
        "trace": trace_dict,
    }
