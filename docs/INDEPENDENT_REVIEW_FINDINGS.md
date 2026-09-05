# Independent review findings

Per the challenge brief's own process checklist ("get a second,
independent AI session, not the one that wrote the code, to critically
review the phase before moving on") -- a step every phase in
[`AWS_ROADMAP.md`](AWS_ROADMAP.md) had listed but none had actually done
until now. An independent model reviewed this repository's code (commit
`36a48a8`), with explicit instructions to verify claims against the
implementation rather than take the documentation's self-assessment at
face value.

This file is the full findings list with disposition. Every "Fixed"
finding was independently reproduced against this repo's own code (or,
for the two harness-measurement findings, against direct API/tool
behavior) *before* being fixed -- see the exact reproduction steps in
[`DECISIONS.md`](DECISIONS.md), which has the full reasoning for each.
This file is the tracker; `DECISIONS.md` is the ADR-style writeup.

Severity labels are the reviewer's own.

## Fixed

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | High | Any authenticated caller could read or cancel any other caller's run by `run_id` alone -- no ownership check existed anywhere. | `auth_context.py` + `owner_sub` on every record, enforced atomically in `get_run.py`/`cancel_run.py`. Live-verified: a fabricated second caller gets 404 against a real deployed run they don't own; the real owner still gets 200. |
| 2 | High | `/ask`, `/runs`, and `/jobs` share one `run_id` keyspace with no conditional writes outside `record_result.py` -- cross-path overwrites, SQS redelivery reopening completed jobs, cancellation silently undone. | Conditional `attribute_not_exists` on every record's creation; `process_job.py`'s writes conditioned on current status; `cancel_run.py`'s conditional update folds in ownership and only attempts `stop_execution` for `execution_type == "STEP_FUNCTIONS"`. Live-verified: a cancelled SQS job stays cancelled after the consumer would have picked it up. |
| 4 | High | Six constructed narrator outputs passed `safety.run_safety_checks` as `safe=True` despite being ungrounded, mis-attributed, or containing dosing/diagnosis language in unmatched phrasings; an empty answer also passed trivially. | `GroundedFact` gained a `unit` field for value+unit pair verification; ordinal-list exemption is now position-based, not value-based; `_NUMBER_RE`'s unit-adjacency bug fixed; diagnosis/dosing pattern lists expanded; added `check_non_empty`. All six original probes re-verified failing against the real pipeline after the fix. |
| 6 | High (for the comparison deliverable) | `stress_test.py --no-retry` didn't actually disable SDK retries (`max_attempts: 1` resolves to `total_max_attempts: 2`). | Fixed to `total_max_attempts=1`; affected burst-sync number re-measured (10/15, matching the original by coincidence -- see `DECISIONS.md`). |
| 9 | Medium | The "3 retry attempts" description of the Step Functions retry policy didn't account for CDK's own default retry policy (6 attempts) sitting ahead of the custom one in the ASL. | Docstrings and docs corrected to describe both policies and how Step Functions resolves the overlap; the previously-published throttling numbers are unaffected (the only error type observed, `TooManyRequestsException`, is exclusively covered by the custom policy). |
| 8 (partial) | Medium (documentation accuracy) | `STRESS_TEST.md` described SQS buffering as providing "guaranteed eventual success at unlimited scale." | Reworded to describe what was actually measured (100/100 at the largest tested burst) rather than an unconditional guarantee; noted the real finite bounds (`maxReceiveCount`, message retention, shared account-wide Lambda capacity). |

## Open (tracked here, not fixed in this pass)

Deliberately out of scope for this pass -- the three High findings above,
plus correcting already-published measurement/documentation claims, were
judged the load-bearing items; these are real but lower-urgency for a
synthetic-data learning project. Each entry below reproduces the
reviewer's original claim; none have been re-verified against current
code the way the "Fixed" table's entries were.

| # | Severity | Finding | Why it's open |
|---|---|---|---|
| 3 | High | `enqueue_job.py`'s DynamoDB write and SQS `send_message` are non-atomic -- a failed send after a successful write leaves a `QUEUED` record with no message behind it, orphaned forever. `process_job.py` also has no reconciliation path for a message that lands in the DLQ (no corresponding terminal-state DynamoDB update). | Needs an outbox-style pattern or an explicit reconciliation sweep, not a quick fix. Noted in `enqueue_job.py`'s own docstring as a known gap. |
| 5 | Medium | `reasoning.py`'s pacing/nutrition branches assert *both* short sleep *and* high stress were reported when the branch actually only requires *either* being present -- a questionnaire with only one of the two still generates a "grounded fact" claiming both. Generic medication/allergy caution categories get converted into specific claims (e.g. levothyroxine, shellfish) that may not match what was actually reported. | Requires auditing every `reasoning.py` branch's claim construction against its actual trigger condition, not a single fix. Not yet started. |
| 7 | High (for the comparison deliverable) | `stress_test.py`'s `burst-async` counts a Step Functions execution status of `SUCCEEDED` as success even when the *application* result inside it was a failure (`RecordFailure`/`RecordTimeout` both end the workflow normally) -- a different success definition than `burst-queue`, which checks the DynamoDB record directly. The adversarial sweep counts HTTP 200, not independently-verified safety correctness. The `race` check only confirms the final status is *some* valid terminal value, not that no double-finalization or later regression occurred. | Needs one consistent success oracle applied across all burst commands, plus separate transport/workflow/application/safety-level reporting, before the comparison table's numbers can be fully trusted at face value. Not yet started. |
| 10 | Medium | `narrator_backend` on a fallback response still reads as the *originally selected* backend (e.g. `"bedrock"`), not `"mock"` -- the only signal of a fallback is a separate `narrator_fallback` entry in `safety_checks`, which the compact DynamoDB record (`adapter.py`'s write) never persists at all. `record_result.py` (Step Functions path) doesn't persist `narrator_backend` either. `PHASE4_BEDROCK_EVIDENCE.md`'s CloudWatch invocation-count argument shows aggregate account-level usage, not caller attribution -- it doesn't by itself rule out the calls having come from somewhere other than the specific Lambda being tested. | The specific evidence already gathered in `docs/PHASE4_BEDROCK_EVIDENCE.md` and `docs/STRESS_TEST.md` did check the full trace (not just the `narrator_backend` field alone) at the time it was gathered, so those specific conclusions hold -- but the *general* pattern is fragile and worth hardening: persist effective backend + fallback reason + model identity on every record, not just the initially-selected backend. Not yet started. |
| 11 | Medium | IAM is resource-scoped but not consistently least-privilege elsewhere: `/ask`'s S3 grant includes read/list/delete/retention actions despite write-only use; DynamoDB grants include scan/delete/batch actions despite `PutItem`-only use on some handlers. The wildcard-resource test only rejects a bare `"*"`, missing wildcards inside lists/nested expressions or action-level wildcards; the S3 auto-delete custom resource's attached `AWSLambdaBasicExecutionRole` includes wildcard-resource CloudWatch Logs permissions (a standard AWS-managed policy, not something this project's own code grants). | Needs auditing every `grant_*` call against the specific actions each handler actually performs, and strengthening the wildcard test to catch nested/action wildcards. Not yet started. |
| 12 | Medium | The ADR justifying ID-token-over-access-token (`DECISIONS.md`, "App Client is a public client... ID token not access token") rests on an incorrect premise -- API Gateway's JWT authorizer does support validating against the `client_id` claim when `aud` is absent, which is exactly Cognito access tokens' shape. Access tokens with API-specific scopes are the more conventional OAuth2 choice for API authorization in the first place. No route currently has scope requirements configured either way. | Would mean reworking `get_dev_token.py`, the authorizer config, and re-verifying the whole Phase 2 login flow -- a real scope change, not a quick correction. The ADR's technical claim is flagged as incorrect here; the underlying design choice hasn't been revisited yet. |
| 13 | Medium | `start_run.py`/`adapter.py` don't validate `run_id`/execution-name character set or length, or bound payload size. `ExecutionAlreadyExists` is treated as success even if the *input* for the reused `run_id` differs from the original request (Step Functions itself distinguishes a matching-input restart from a genuinely conflicting one; `start_run.py` doesn't check either way and always reports `RUNNING`, even for an execution that's actually already finished). | Needs an idempotency-fingerprint comparison and Step Functions' own execution-status distinction wired through. Not yet started. |
| 14 | Medium | `test_live_endpoint_smoke.py` still asserts the deployed backend is `mock`, though the real deployed Lambdas default to `bedrock` since Phase 4/the stress-test pass -- the test is stale relative to current deployed reality. The retry-policy test only checks that the custom policy is *present*, not that it's the one that actually takes precedence for a given error (see finding #9). The stack-dependency test manually calls `add_stack_dependency` and then asserts the dependency exists -- it verifies the API works, not that `app.py` actually wires the dependency in the real app. | Each is a real gap in what the test suite actually proves, not a bug in the application itself. Auditing and fixing all of `infra/tests/` for this class of issue is its own pass. Not yet started. |
| 15 | Low | The real AWS account ID (`470293170577`) is committed in `auth_stack.py`'s hardcoded Cognito domain prefix and referenced in `DECISIONS.md`. Not a credential and doesn't grant access on its own, but unnecessary disclosure and hurts portability (anyone forking this repo needs to change it before they can deploy). | A quick fix (parameterize the domain prefix, e.g. via CDK context or an env var with a documented default), but genuinely low-urgency -- account IDs aren't secrets, and this repo already disclosed the reasoning for hardcoding it (Cognito domain prefixes must be globally unique) in `DECISIONS.md`. Not yet started. |

## What the review said to keep as-is

Quoted from the review, not re-verified independently here but consistent
with what this project already believed about its own design: the
reasoning/narration separation and optional deterministic path; the
five-stack decomposition (the coupling problem was shared mutable state,
not stack count); BM25 and read-only parameterized SQLite access; the S3/
SQS storage baseline (encryption, public-access blocking, TLS
enforcement); conditional terminal writes in the Step Functions path
(`record_result.py`) as the pattern worth extending everywhere, which
finding #2's fix above now does; and the experiment-driven approach to
finding real throttling/concurrency behavior in general.
