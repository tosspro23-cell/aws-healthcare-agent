"""Validates a caller-supplied `run_id` against Step Functions' own
execution-name constraints.

`/ask`, `/runs`, and `/jobs` share one `run_id` keyspace (see
`docs/DECISIONS.md`), and only the `/runs` path actually uses `run_id` as
a Step Functions execution name -- but a `run_id` that's invalid for that
purpose used to only fail *there* (`start_run.py`, as an uncaught boto3
`ClientError`; see the earlier fix for that in `docs/DECISIONS.md`).
Since all three paths can be handed the same `run_id` value later (a
client that starts a run via `/ask` might reasonably expect to poll or
cancel it via `/runs/{run_id}`), validating the same constraint
consistently across all three creation points means an accepted `run_id`
is guaranteed usable everywhere, not just on the path that happened to
create it.

Constraints per AWS's Step Functions `StartExecution` documentation: a
name must be 1-80 Unicode characters, and must not contain whitespace,
the characters ``< > { } [ ] ? * " # % \\ ^ | ~ ` $ & , ; : /``, or
control characters (U+0000-001F or U+007F-009F). This module doesn't
invent a stricter rule than that -- an independent review flagged the
*absence* of any check at all, not a request for a narrower one.
"""

from __future__ import annotations

import re

_DISALLOWED_CHARS_RE = re.compile(r'[\s<>{}\[\]?*"#%\\^|~`$&,;:/\x00-\x1f\x7f-\x9f]')


def is_valid_run_id(run_id: str) -> bool:
    return 1 <= len(run_id) <= 80 and not _DISALLOWED_CHARS_RE.search(run_id)
