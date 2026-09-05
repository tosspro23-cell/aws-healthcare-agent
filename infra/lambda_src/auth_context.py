"""Extracts the authenticated caller's identity from an API Gateway HTTP
API (v2) event whose route has the Cognito JWT authorizer attached (every
route in this project -- see `../stacks/api_stack.py`).

Why this needs to exist at all: API Gateway's JWT authorizer validates a
token's signature/expiry/audience *before* a request ever reaches a
Lambda, but that only proves "this is a genuine, unexpired token for this
app client" -- it says nothing about which `run_id`s the token's holder
is entitled to read or cancel. Before this module existed, every run
handler (`adapter.py`, `get_run.py`, `cancel_run.py`, `start_run.py`,
`enqueue_job.py`) trusted whatever `run_id`/`user_id` the caller put in
the request body or path, with no check against who actually made the
request -- any authenticated caller could read or cancel any other
caller's run by `run_id` alone. See `docs/INDEPENDENT_REVIEW_FINDINGS.md`
(finding #1) and `docs/DECISIONS.md` for the incident this fixes.

The JWT `sub` claim (a stable, unique identifier for the Cognito user,
never reused even if the user changes their email) is the actual
authorization principal -- not `user_id`, which is a caller-supplied
field naming *whose synthetic health-data profile* a question is about
and is not itself a proof of identity.
"""

from __future__ import annotations


def owner_sub_from_event(event: dict) -> str:
    """Raises KeyError if the claim is missing -- every route this is
    used on already requires the JWT authorizer to have run first, so a
    request that reaches here without a `sub` claim indicates a
    misconfigured route, not a legitimate anonymous caller. Failing loudly
    (a 500, via the caller's own broad exception handling) is the correct
    behavior for that case, not silently skipping the ownership check.
    """
    return event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
