"""SQS-triggered Lambda: consumes messages that exceeded
`process_job.py`'s max delivery attempts and landed in the dead-letter
queue (`queue_stack.py`'s `JobsDLQ`), marking the corresponding run
FAILED.

Without this, a run whose message got DLQ'd has nothing left in the
system that will ever write a terminal status for it -- the record just
stays wherever `process_job.py`'s last attempt left it (`QUEUED` if
every attempt died before its first `RUNNING` write, `RUNNING` if an
attempt died mid-processing), and a caller polling `GET /runs/{run_id}`
never sees a resolved status. An independent review found this exact
gap and left it deliberately open pending a real design rather than a
quick patch -- see `docs/INDEPENDENT_REVIEW_FINDINGS.md` (round 2,
finding #9) and `docs/DECISIONS.md`.

The write is conditioned on the record still being non-terminal (QUEUED
or RUNNING) -- the same race-safety pattern `process_job.py`'s own
writes already use -- so a legitimate SUCCEEDED/FAILED/CANCELLED outcome
that happened to land right as the DLQ redrive fires is never clobbered.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from run_writes import conditional_status_write


def handler(event: dict, context: object) -> None:
    for record in event["Records"]:
        message = json.loads(record["body"])
        run_id = message["run_id"]
        conditional_status_write(
            run_id,
            if_status_in=("QUEUED", "RUNNING"),
            status="FAILED",
            error_message="Job exceeded max delivery attempts and moved to the dead-letter queue.",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
