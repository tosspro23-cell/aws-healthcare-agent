# Design Decision Log

Lightweight ADR-style log, one entry per non-obvious decision, added as the
AWS build-out progresses (see [`AWS_ROADMAP.md`](AWS_ROADMAP.md) for phase
status). Newest entries at the top. The point of keeping this is to have a
concrete artifact to compare against the equivalent decisions made on the
other cloud, not just a mental note of "why we did it this way."

---

## 2026-09-05 — Third independent review: a reopened safety bypass, a within-topic overclaim, and four frontend fixes

**Context**: Requested specifically to cover what round 1 and round 2
never touched -- the entire frontend and the newly-public S3+CloudFront
hosting from the same day's earlier Phase 6 work -- plus verification of
several hotfixes made live during that work. All 8 findings were
independently reproduced against this repo's own code before being fixed
here, the same standard rounds 1 and 2 used.

**Finding 1 (High, safety bypass reopened)**: A live hotfix made earlier
the same day added a `question_text` exemption to
`verify_numeric_grounding` -- a bare number was accepted as grounded if
the caller's own question already used it, meant to stop a real false
positive where the model declined while citing the question's own
number. The review reproduced two ways this reopened a genuine
fabrication bypass: `"Is my cardiovascular risk score 999?"` ->
`"Your... risk score is 999."` now passed even though 999 is never a
real grounded value (the exemption can't distinguish declining-while-
citing from affirming-while-fabricating); and irregular spacing
(`"500  mg/dL"`, two spaces) or Markdown emphasis (`"**500** mg/dL"`)
made the *strict* value+unit regex fail to match, silently falling
through to the now-exempted weak path instead of being checked against
real grounded values at all. **Decision: reverted the exemption
entirely** rather than patching the regex further -- reliably telling
"declining while citing a number" from "asserting that number as fact"
isn't solvable with a regex, and the asymmetry matters: a false positive
here just means a safe answer gets replaced by the deterministic
template, while a false negative means a fabricated clinical number
reaches the user. Confirmed via direct reproduction in Python before and
after the revert. `run_safety_checks`'s `question_text` parameter was
removed along with it (both `agent.py` call sites updated).

**Finding 7 (Medium, within-topic overclaim)**: `mock_narrator.py`'s
closing personalization summary hardcoded `"nutrition" in topics or
"exercise_volume" in topics` into one combined phrase ("leans on your
stated food and activity preferences") regardless of which of the two
actually fired -- the same "policy written for one case, applied to a
different case" failure mode round 2 already fixed for other topics, not
extended to this pair. **Decision**: split into two independent
branches, each using the specific detail already present in that
modifier's own grounded-fact claim (a new `_claim_detail()` helper) so
the visible sentence names the actual reported signal instead of an
assumed one. **A real regression introduced while fixing this, caught by
the full suite before considering the fix done**: the `exercise_volume`
modifier's claim text ("...less than 60 minutes...") embeds a literal
number never registered as grounded, so reusing that text in the closing
summary broke `numeric_grounding` for 10 previously-passing tests
(`ungrounded numbers: ['60']`). Fixed in `reasoning.py` by adding
`numeric_values=_numbers_in(claim)` to that fact's construction, the
same pattern finding #8 of round 2 already established for exactly this
situation.

**Findings 2-5, 8 (frontend, never previously reviewed)**: (2) Run
history (`localStorage`, full question text) persisted across accounts
in a shared browser -- `signOut()` now also calls a new
`clearHistory()`. (3) `AskForm`'s polling used `setInterval(async () =>
...)`, which doesn't wait for the previous tick's request to resolve --
a slower, earlier response could resolve after a faster, later one and
overwrite already-terminal state with stale data. Rewritten as a
self-scheduling `setTimeout` (next tick only scheduled after the current
one resolves) plus a monotonic generation counter checked before and
after each async call, so a superseded poll loop's in-flight request can
never write state again. (4) A polling error that wasn't tolerated (not
the expected transient 404 right after a Step-Functions start) set
`error` but never reset the derived `pending` flag, permanently
disabling submit and mode-switching with nothing left running; a new
`pollingStalled` state overrides `pending` in that case. `handleCancel`
also now applies the real post-cancel state immediately (refetching via
`getRun`) instead of waiting for a poll tick that might never come if
polling had already stopped or the run raced to a terminal state.
(5) `react-markdown` renders `![alt](url)` as a real `<img>` the browser
eagerly fetches with no user interaction -- a data-exfiltration vector
for any app whose text ultimately originates from an LLM completion.
Given a `components` override for `img` (same pattern already used for
`a`), rendering a plain `[image: alt -- not rendered]` badge instead.
(8) A stored access token was treated as valid regardless of age; an
expired token just failed every API call with a 401 the app never
noticed, leaving the signed-in Workbench displayed indefinitely.
`completeSignIn` now records `Date.now() + expires_in * 1000` alongside
the token; `getAccessToken()` self-clears an expired one, and
`authedFetch` forces a hard redirect to `/` on any 401 (a revocation
server-side can't be caught by the client's own expiry bookkeeping
alone).

**Finding 6 (get_run.py, deployed before the rest of this round)**: the
`AccessDenied`-for-missing-trace fix from the same day's earlier work
only wrapped the `get_object()` call itself in its `try` block --
reading the response body (`.read()`, which can raise `ReadTimeoutError`,
a `BotoCoreError` subclass with no `.response` attribute, distinct from
`ClientError`) and `json.loads()`-ing it happened *after* the `except`,
unprotected. A transport-level failure while streaming an otherwise-
successful `get_object()` response still 500'd the whole `GET /runs/
{run_id}` endpoint. Fixed by moving both calls inside the `try` and
catching `BotoCoreError`/`ValueError` alongside `ClientError`.

**A self-inflicted deploy bug, caught during this round's own live
verification, not left in**: redeploying all six stacks with `cdk
deploy --all` and no `CARE_AGENT_WORKBENCH_URL` set silently reset
`ApiStack`'s CORS `allow_origins` back to its bare default
(`["http://localhost:8765"]`, see `app.py`'s two-pass deployment
pattern), dropping the live CloudFront origin from the allowlist and
breaking every API call from the public URL with a browser-level
`TypeError: Failed to fetch` (a preflight `OPTIONS` response with zero
CORS headers). Caught immediately by testing the redeployed public URL
in a real browser rather than only the local dev server. Fixed by
redeploying `CareAgentAuthStack` and `CareAgentApiStack` a second time
with `CARE_AGENT_WORKBENCH_URL` set to the live `WorkbenchUrl` output --
confirmed via a live CORS preflight (`curl -X OPTIONS`) that
`Access-Control-Allow-Origin` for the CloudFront origin is present
again. This is a deployment-process gap worth remembering, not a code
bug: `app.py`'s docstring already documented the two-pass requirement,
but nothing enforces it at deploy time -- a future `cdk deploy --all`
run the same way will reproduce this exact regression.

**Verification**: Full kernel suite (154 tests, up from 152 before
finding 7's fix; coverage 90.67%, gate 85%) and full infra suite (140
tests, up from 139) pass; `ruff`/`mypy` clean on both; frontend
`npm run build` and `cdk synth --all` (6 stacks) both clean. All 6
stacks redeployed live. Live-verified in a real browser against both
`localhost:8765` and the public CloudFront URL: history is empty
(`localStorage.getItem(...)` returns `null`) immediately after sign-out
(finding 2); a real Step-Functions run's `Cancel this run` raced against
natural completion, returned `409: Run was already finalized`, and the
UI correctly displayed the real `SUCCEEDED`/`SAFE` terminal state instead
of getting stuck (finding 4, and indirectly finding 3 -- the same run
polled cleanly through `RUNNING` to a terminal state with no stale
overwrite); an explicitly-expired token blocked a submit with a clean
"Not signed in." before ever calling `fetch` (finding 8, forced); and,
unforced, a real token that had aged out over the course of this
session's own work hit the live redeployed API, got a real 401, and
`handleSessionExpired()` correctly forced a clean return to the sign-in
screen rather than leaving the signed-in shell stuck -- the same fix
confirmed twice, once deliberately and once by accident. Finding 5 (the
image-suppression badge) is verified by code reading and the identical,
already-proven `a`-override pattern, not by a live malicious-image
probe. Finding 1's revert is verified via direct reproduction and the
kernel test suite (including a Bedrock-narrator-backed regression test),
not via a live Bedrock call deliberately trying to reproduce the exact
fabrication -- inherently non-deterministic to force on demand, and no
previous round's safety-check verification relied on that either.

---

## 2026-09-05 — Phase 6 finished out: async trace persistence, markdown rendering, public hosting

**Context**: Requested as the last increment on the Workbench: (1) give
the async paths the same full grounding trace the sync path already has,
(2) render Bedrock's markdown prose instead of showing literal syntax,
(3) host the Workbench somewhere real instead of only `npm run dev`,
explicitly as "a step toward a real user-facing product" -- with two
requirements attached: self-sign-up must be off first (a public link
shouldn't let strangers create accounts, even against synthetic data),
and the layout needed to actually work on a phone, not just a desktop
dev browser.

**Decision, trace persistence**: `agent_task.py` and `process_job.py`
now write their full trace to the same `{run_id}.json` S3 key
`adapter.py`'s synchronous path already used, with the same
precisely-scoped `s3:PutObject` grant pattern this project has used
throughout. `get_run.py` opportunistically merges that trace in under a
`trace` key.

**A second real bug found live, not a hypothetical**: `get_run.py` is
deliberately granted only `s3:GetObject` (not `s3:ListBucket`, which
would let it enumerate every run_id's evidence in the bucket -- a much
bigger permission than "read one object I already know the key for").
Without `ListBucket`, S3 can't tell a caller whether a missing key
doesn't exist or is merely inaccessible, so it returns `AccessDenied`
instead of `NoSuchKey`/404 for a run whose evidence hasn't been written
yet. This propagated uncaught and 500'd the *entire* `GET /runs/{run_id}`
response -- including the DynamoDB status/answer, which were perfectly
fine and had nothing to do with the missing trace. Reproduced against the
real deployed account (moto doesn't enforce IAM, so this couldn't have
been caught there) via the Workbench's own polling hitting it mid-run.
Fixed by treating *any* S3 read failure for this specific, best-effort
enrichment as "no trace yet" -- it sits on top of DynamoDB's record,
never replaces it as the source of truth, so a failure here should never
be allowed to take down the whole response.

**Decision, markdown rendering**: `react-markdown` (never executes raw
HTML -- parses to React elements, appropriate since this text ultimately
originates from an LLM completion, not fully trusted content even though
the safety pipeline already constrains its factual claims).

**Decision, public hosting**: `FrontendStack` (S3 + CloudFront, Origin
Access Control, no public bucket policy, no website-hosting endpoint) --
built via a `build_frontend_asset.py` mirroring `build_lambda_asset.py`'s
existing pattern. Self-sign-up disabled on the User Pool
(`self_sign_up_enabled=False`) before the URL went live -- confirmed
visually (the Hosted UI's "Sign up" link is gone) and via
`AllowAdminCreateUserOnly: true` on the real user pool; no test or flow
in this project ever depended on self-sign-up staying on. A mobile pass
(flex-wrapping mode tabs, 16px form inputs to avoid iOS Safari's
zoom-on-focus, 44px touch targets, a narrow-viewport media query) --
verified on an emulated 375px viewport against the real hosted URL.

**The two-pass deployment problem**: `FrontendStack`'s CloudFront domain
isn't known until after its first deploy, but `AuthStack`'s Cognito App
Client and `ApiStack`'s CORS both need that exact domain registered
before a browser served from it can complete a real login or call the
API. Solved with an optional `CARE_AGENT_WORKBENCH_URL` env var
`app.py` threads into both stacks -- unset for the first deploy, set to
the printed `WorkbenchUrl` output for the second. The frontend's own
redirect/logout URI is derived from `window.location.origin` rather than
hardcoded, so the exact same build works unmodified on `localhost:8765`
and the hosted URL -- no separate "production build" was needed.

**Verification**: Full kernel/infra test suites pass (infra: 139 tests,
up from 130, including new `test_frontend_stack.py` and regression tests
for both the trace-merging behavior and the `AccessDenied` fix). `cdk
synth --all` succeeds (6 stacks now). Deployed live in two passes;
confirmed via `describe-user-pool-client` that both `localhost:8765` and
the CloudFront URL are registered as callback/logout URLs, via
`describe-user-pool` that `AllowAdminCreateUserOnly` is `true`, and via a
live CORS preflight that the CloudFront origin is allowed. End-to-end in
a real browser against the live public URL: sign-in through the actual
Cognito Hosted UI (a human completed the credential entry, per this
project's standing constraint), a real `/ask` call with markdown
rendering, a Step-Functions run showing its full trace after the
`get_run.py` fix (reproduced the 500 live first, then confirmed the fix
against the same run_id), and the whole page checked on an emulated
375px mobile viewport.

---

## 2026-09-05 — `cancel_run.py`'s two conflict responses used "message" instead of "error"

**Context**: User-reported from the Workbench: a `Cancel this run` click
that lost the race (the run had already finished, or was a synchronous
run that can't be cancelled at all -- both legitimate, correctly-detected
409 outcomes) showed a bare "409: Request failed with status 409" with no
explanation. Every other error response in this API -- `start_run.py`,
`enqueue_job.py`, `adapter.py`, `get_run.py`, and even `cancel_run.py`'s
own 404 -- puts the human-readable reason under an `"error"` key; only
these two specific 409 responses used `"message"` instead. The frontend
(correctly, matching the API-wide convention) reads `body.error`, so it
silently got nothing for exactly these two cases.

**Decision**: Renamed both fields from `"message"` to `"error"`, matching
every other response in the API. No client depended on the old key name
(checked: no test asserted on it).

**Verification**: New assertions in `tests/test_orchestration_lambdas.py`
(`test_cancel_run_refuses_to_cancel_a_synchronous_ask_run` and
`test_cancel_run_loses_race_when_already_finalized`) that `"error"` is
present in the response body. Full infra suite (130 tests) passes.
Redeployed `CareAgentOrchestrationStack` and live-verified directly
against the deployed `CancelRunHandler`: cancelling a
directly-seeded already-`SUCCEEDED` run now returns `{"error": "Run was
already finalized; nothing to cancel.", ...}`, not a bare status code.

---

## 2026-09-05 — Workbench: wired up the async paths (Step Functions + Queue), client-side run history

**Context**: The Workbench's first version covered only the synchronous
`/ask` path. Requested next: bring the async Step Functions and SQS
paths, cancellation, and persistence into the UI too, so the Workbench
covers what the backend phases actually built rather than just the
simplest path.

**Decision**: One form, a mode switcher (`Ask` / `Start run (Step
Functions)` / `Enqueue job (Queue)`) instead of three separate pages --
all three share the same user_id/question inputs and differ only in
which endpoint starts the run and whether polling is needed. The two
async modes poll `GET /runs/{run_id}` every second until a terminal
status, with a `Cancel this run` button visible while pending. Run
history is client-side only (`frontend/src/history.ts`, localStorage):
a `run_id` list per browser lets a past run be revisited via `GET /runs/
{run_id}` after a reload, which demonstrates real DynamoDB persistence
without building a new backend "list my runs" endpoint -- the runs
table's only key is `run_id`, so that would need a new GSI + Lambda +
route, a meaningfully larger piece of infra work than wiring up what
already exists. Left for later if it's ever worth doing.

**A real race condition found live-testing this**: `POST /runs` returns
as soon as Step Functions accepts `start_execution`, before its first
task (`mark_running.py`) has actually written the DynamoDB record --
polling immediately after start reliably produced a real `404`
(reproduced, not theoretical). Fixed by tolerating a bounded run of
404s (10 poll ticks) at the start of a poll loop rather than treating an
immediate fetch as authoritative, and by not doing a blocking `getRun`
call synchronously right after starting -- the UI shows an optimistic
pending state from the start call's own response instead. The SQS path
doesn't have this specific race (`enqueue_job.py`'s conditional create
happens synchronously before it returns 202), but the fix applies to
both paths uniformly rather than branching on `execution_type`, since
tolerating a transient 404 is harmless either way.

**Verification**: `tsc -b` and `eslint .` clean, production build
succeeds. Live end-to-end in a real browser (signed in through the
actual Cognito Hosted UI): both async modes correctly transitioned
`RUNNING`/`QUEUED` -> `SUCCEEDED` with the real Bedrock-backed answer;
clicking a past history entry after a full page reload correctly
re-fetched and displayed it, proving the data survives independent of
any client-side state. Cancellation was verified via the exact HTTP
calls the UI's `Cancel this run` button makes (a manual click reliably
lost the race against Bedrock's ~1-2 second response time -- a
UI-testing limitation, not a functional gap, and the underlying
conditional-write mechanism was already race-tested repeatedly earlier
in this project): started a run, cancelled it immediately, and confirmed
the record stayed `CANCELLED` -- not overwritten by the agent's own
completion -- when re-checked 3 seconds later, well past when it would
normally have finished.

---

## 2026-09-05 — Numeric grounding rejected a model correctly declining to fabricate a number

**Context**: Testing fallback behavior via the Workbench, asked Bedrock to
"calculate my 10-year cardiovascular risk score" -- something this
project's data and policies don't support computing. Bedrock did the
right thing: it declined, explicitly saying a real risk-score calculation
needs a validated clinical tool and more inputs than are available. This
safest-possible response still failed `numeric_grounding` and got
replaced by the (objectively worse in this instance) mock template --
because "10" (from the user's own "10-year" phrasing, referenced back
while explaining the refusal) matched no `GroundedFact`. The model hadn't
invented anything; it echoed a number the caller had already introduced.
User-reported directly from a live Workbench session, then independently
reproduced across several other questions (asking for reference ranges,
population comparisons) that provoke the same shape of false rejection.

**Decision**: `verify_numeric_grounding`/`run_safety_checks` now accept
the original `question_text` and add any bare (no-unit) number *the
caller already used* to the weak grounding set. Deliberately narrow:
this only touches the weaker, no-unit-attached check (already documented
as incomplete -- "unavoidable for numbers with no unit to bind against").
The strict value+unit path (e.g. "your LDL is 500 mg/dL") is completely
unaffected even if a question happens to mention that same number --
verified directly with a test that a false value+unit claim is still
rejected when the question also contains the number.

**Verification**: New tests in `tests/test_safety.py` (the exemption, and
that it doesn't weaken the value+unit path) and
`tests/test_bedrock_narrator.py` (full agent-level reproduction of the
exact reported scenario -- now `safe=True`, `narrator_backend="bedrock"`,
no fallback). Full kernel suite (152 tests, up from 149) and infra suite
(130 tests) both pass. Live-verified against the deployed `AskHandler`
after redeploying all 5 stacks: the exact reported question, run 3 times,
stayed on `bedrock` with no fallback every time. Also confirmed the
already-verified genuine fallback cases (asking for a reference range
with units, e.g. hs-CRP "1.0-3.0 mg/L") still correctly fall back --
the strict path is unaffected.

---

## 2026-09-05 — Fallback debug visibility, mechanical-sounding wording, and a red-flag gap for headaches

**Context**: Three separate pieces of feedback from testing the
Workbench directly: (1) when a fallback happened, there was no way to see
what the rejected draft actually said or precisely why -- the
`narrator_fallback` entry just said "failed a safety check," full stop;
(2) the composed answers still read as templated/mechanical in a couple
of specific spots; (3) "I'm having big head pain, what should I do?"
classified as `priority_focus`, not `red_flag_emergency` -- worth
checking whether that's a real gap.

**Decision, fallback visibility**: `AgentTrace` gained `rejected_draft:
str | None`, populated with the discarded narrator output whenever a
fallback happens; the `narrator_fallback` safety-check detail now names
which specific check(s) failed and why, not just "a safety check." Never
surfaced as the answer -- only as debug/trace information, consistent
with this project's existing "expose enough trace/debug information"
design goal.

**Decision, wording**: Two real issues found while investigating, not
just subjective polish: (a) `reasoning.py`'s exercise-limitation and
family-history modifiers (fixed earlier the same day to render the
caution's actual reported detail instead of a hardcoded specific claim)
embedded that detail text verbatim mid-sentence ("given the reported
exercise limitation: Reports knee pain..."), which reads grammatically
broken -- a `_naturalize_detail()` helper now strips the leading
"Reports "/trailing period and lowercases it for natural mid-sentence use.
(b) `mock_narrator.py`'s closing "Your questionnaire answers changed this
plan..." sentence was a single hardcoded string emitted whenever *any*
questionnaire modifier fired, unconditionally naming knee pain/sleep/
stress regardless of which modifiers actually applied -- only
coincidentally correct against the shipped sample data, where every
modifier always fires together. This is the same hardcoded-regardless-
of-trigger failure mode as several findings from the two independent
reviews, just not caught there since it lived in the narrator, not
`reasoning.py`. Rebuilt to name only the modifiers actually present,
joined with proper "A, B, and C" list grammar instead of a repeated
"; and" chain (which itself read mechanically once 3+ parts existed).

**Decision, red-flag headache gap**: `_RED_FLAG_PATTERNS` had no
headache-related coverage at all. Added three specific, medically-
established emergency-headache phrasings (`worst headache`, `sudden
severe headache`, `thunderclap headache`) -- deliberately not a bare
"headache"/"head pain" pattern, since an ordinary headache is common and
not itself an emergency; flagging every mention would make the system
wrongly tell people to go to the ER constantly, and breaks the existing
list's own scoping principle (specific established phrasings only, e.g.
"chest pain" is present but a generic "chest discomfort" is not). This
means a vague phrasing like "big head pain" deliberately still does not
trigger red_flag_emergency after this fix -- flagged to the user as an
explicit product-judgment boundary, not silently decided.

**Verification**: New tests in `tests/test_intent.py` (headache
red-flag, and the "big head pain" non-match documenting the boundary),
`tests/test_mock_narrator.py` (new file: personalization summary omitted
with no modifiers, and only naming modifiers that actually fired), and
an extended `tests/test_bedrock_narrator.py` fallback test asserting
`rejected_draft` and the enriched failure detail. Full kernel suite (152
tests) and infra suite (130 tests) pass. Live-verified against the
deployed `AskHandler`: "worst headache of my life" now returns
`red_flag_emergency` with an emergency-care answer; "big head pain"
still returns `priority_focus`, confirmed as the intended boundary, not
an oversight.

---

## 2026-09-05 — Found via the Workbench itself: "vitamin" hijacked trend questions into supplement_safety

**Context**: Testing the newly-built Workbench end to end, a real question
-- "Has my vitamin D changed since last time?" -- was classified as
`supplement_safety`, not `trend_check`. Cause: `intent.py`'s
`_SUPPLEMENT_PATTERNS` included a bare `\bvitamin\b` pattern, checked
before trend/priority patterns get a chance -- since "Vitamin D" is also
this project's biomarker name, *any* question naming that marker (trend,
priority, or otherwise) got force-classified as supplement_safety. Trend
computation (`compute_trend`) never ran, leaving `Brief.trend_result`
unset. The LLM narrator filled that gap with an unverified prose claim
("I don't have a previous vitamin D result to compare") -- which
happened to be factually correct this time (the earlier panel genuinely
has no Vitamin D reading, confirmed against `data/sample_bloodwork.json`
directly), but was never actually checked by anything in the pipeline.
This is a live instance of a more general, already-acknowledged gap: the
safety checks verify *numbers*, not arbitrary narrative/procedural claims
a narrator might add -- the same underlying limitation as the still-open
cross-marker value/unit binding gap. Not fixed further here; noted as the
same class of issue.

**Decision**: Split `_SUPPLEMENT_PATTERNS` into the genuinely strong
supplement/dosing signals (`supplement`, `dose`, `dosage`, `pill`,
`mg of`) and a separate, weaker `_MARKER_NAME_ONLY_PATTERNS` (`vitamin`
alone). The weak pattern only wins as `supplement_safety` when
trend/priority language isn't *also* present in the same question --
otherwise trend_check gets the chance it should have had. A bare
supplement question with no trend language ("what vitamin should I take
for my low levels?") is unaffected and still classifies as
`supplement_safety`, matching prior behavior.

**Verification**: New tests
`test_vitamin_d_trend_question_is_trend_check_not_supplement_safety` and
`test_vitamin_supplement_question_without_trend_language_is_still_supplement_safety`
(`tests/test_intent.py`) cover both directions. Full kernel suite (143
tests, up from 141) passes. Re-ran the exact original question locally
(`intent: trend_check`, and the answer's data-unavailability claim is now
sourced from `trend.reason_unavailable`, a real computed field, not an
LLM guess) and against the live deployed `AskHandler` after redeploying
all 5 stacks (`safe: true`, `intent: trend_check`, a grounded answer
explicitly citing the checked prior panel).

---

## 2026-09-05 — Phase 6 Workbench: minimal React/Vite frontend, scoped to `/ask` first; required adding CORS

**Context**: Every phase through the stress-test pass and both
independent-review rounds was backend-only -- real Cognito auth existed,
but the only callers were terminal tooling (`curl`, `pytest`,
`get_dev_token.py`). The Azure counterpart already has a React/Vite
Workbench; building the AWS equivalent is the natural next comparison
point (same API, different cloud's auth/client story), and it also turns
this project's own login/ask/trace-inspection loop into something usable
by a person, not just provable via a terminal.

**Decision**: Scoped the first version tightly rather than building the
whole API surface at once: real Authorization Code + PKCE through an
in-browser redirect to the Hosted UI (not a script standing in for one),
`POST /ask` only (not the async `/runs`/`/jobs` paths yet), and a full
render of the answer plus its grounding trace (safety checks, grounded
facts, limitations, sources). Plain React + Vite + TypeScript, no router
library (one `pathname === "/callback"` check covers the only extra
route this needs), no state-management or component library -- matching
the kernel/infra's own dependency discipline. Runs as a local dev server
on a fixed port (8765) that exactly matches the one redirect URI already
registered on the Cognito App Client, so no App Client change was needed.

**A real infra gap this surfaced**: API Gateway had never had a browser
caller before, so `ApiStack`'s `HttpApi` had no CORS configuration at
all -- every prior caller (curl, pytest, boto3) is same-origin-exempt by
construction. Added `cors_preflight` scoped to exactly
`http://localhost:8765` (not a wildcard), since that's the only origin
that's real right now; will need widening once a real hosted Workbench
URL exists. New test: `test_stacks.py::test_http_api_has_cors_scoped_to_the_workbench_dev_origin_not_a_wildcard`.

**A real bug this surfaced, live, that no unit test would have caught**:
React 18's `<StrictMode>` deliberately double-invokes effects in
development specifically to catch exactly this class of bug -- the
`/callback` route's `useEffect` called `completeSignIn(code)` twice on
the same mount, and an OAuth authorization code is single-use, so the
second exchange failed with a real `400 invalid_grant` from Cognito.
Harmless in this instance (the first exchange had already stored the
token before the second one's failure was handled), but a genuine race,
not a false alarm -- confirmed by checking the console log's full
history: exactly one `400`, timestamped before the fix's hot-reload, none
after across multiple subsequent sign-ins. Fixed with a `useRef` mount
guard, the standard pattern for a legitimate one-time side effect under
StrictMode.

**Verification**: `tsc -b` and `eslint .` both clean. `npm run build`
succeeds (150KB JS, gzipped ~49KB). Full live end-to-end test in a real
browser: sign-in through the actual Cognito Hosted UI (a human completed
the credential entry, per this project's standing constraint that
interactive Cognito login can't be automated), a real `/ask` call
answered by the deployed Bedrock-backed Lambda, `safe: true`, all 4
safety checks shown passing, 12 grounded facts rendered with their
sources -- and the fix re-verified across two additional sign-in/sign-out
cycles with zero new console errors. Infra regression suite (130 tests,
up from 129) and `cdk synth --all` both still pass with the CORS addition
in place; `CareAgentApiStack` redeployed live.

**Consequence**: The async paths, a run-history view, markdown rendering
for LLM-narrated answers (Bedrock's prose includes literal `**bold**`
markers, currently shown as-is), and real hosting (S3+CloudFront, needing
a second registered Cognito callback URL) are explicitly not done --
tracked in `docs/AWS_ROADMAP.md`'s Phase 6 section as open, not silently
implied to be finished.

---

## 2026-09-05 — A second independent review, scoped to *verify* round-1's fixes, found real regressions in them; fixed

**Context**: After the first independent review's 13 findings were fixed
(see the entries below), a second independent review was deliberately
scoped as a verification pass rather than a from-scratch re-scan: for
each "fixed" finding, does the fix actually close the gap, or only the
specific reproduction originally reported -- and did fixing it introduce
a new problem? Every claim below was independently reproduced against
this repo's own code before being trusted, the same standard applied
throughout this project.

**What it found, confirmed real**:

1. **A regression that broke ordinary, previously-safe answers.**
   Finding #4's value+unit binding fix (see the numeric-grounding entry
   below) required a `GroundedFact.unit` to be populated for the strict
   check to apply -- but the trend intent's `latest value`/`previous
   value` facts were never given one, even though `trend.py` already
   computed it. Reproduced live against this project's own sample data at
   commit `3985ce4`: `Is my LDL getting worse?`, `Is my HbA1c getting
   worse?`, and `What is my eGFR?` all returned `safe=False`, each for a
   different reason under the same root cause (a value's only grounding
   source lacked a unit) plus a second bug (the eGFR unit string
   `mL/min/1.73m2` contains its own digits, which the fallback bare-number
   scan re-discovered as a second, unrelated "ungrounded number" because
   only the *value*'s span, not the full value+unit match, was excluded
   from that scan).
2. **A synchronous run's cancellation could be silently undone.** The
   ownership+status condition added for finding #1 never excluded
   `execution_type = "SYNC"`, so a `/ask` run in flight could be marked
   `CANCELLED` -- and then have that overwritten back to `SUCCEEDED`/
   `FAILED` the moment `adapter.py`'s own unconditional terminal write
   landed, since there was never an execution to actually stop.
3. **A new IAM gap in code added to close finding #13.** `start_run.py`'s
   `ExecutionAlreadyExists` handling calls `DescribeExecution` to compare
   the existing run's real input, but `StartRunHandler`'s role only ever
   had `StartExecution`. This would `AccessDenied` on every duplicate
   `run_id` submission against the real account -- invisible to
   moto-mocked tests, which don't enforce IAM.
4. **The finding-#3 compensating write could clobber a real outcome.** An
   SDK exception from `send_message` doesn't prove SQS rejected the
   message -- it can mean the send succeeded and only the *response* was
   lost, in which case a consumer could already be processing or have
   finished the job. The unconditional compensating write would clobber
   that back to `FAILED`.
5. **A new inconsistency in `adapter.py`'s write order.** The DynamoDB
   record was marked `SUCCEEDED` before the S3 evidence write, with no
   handling if that write then failed -- leaving a record permanently
   claiming success with no evidence, and (because the conditional-create
   guard added for finding #2 now blocks it) not retryable under the same
   `run_id`.
6. **Finding #4's fix incompletely closes the original gap.** The
   value+unit check verifies *some* fact carries that exact pair, not
   that the text's claimed marker is the one that actually has it -- two
   markers sharing a unit (LDL/HDL/triglycerides/total cholesterol are
   all `mg/dL`) can still be swapped without detection.
7. **Finding #5's fix was incomplete.** It corrected the pacing/nutrition
   modifiers' `GroundedFact.claim` (metadata) but left `text` -- what the
   narrator actually renders -- still unconditionally naming both
   signals. The existing regression test for this only asserted on
   `claim`, not `text`, so it passed despite the bug. Separately, the
   medication/allergy cautions treated a bare substring match as positive
   evidence, so a denial ("Patient denies levothyroxine use") still
   produced an affirmative claim.
8. **The same bug pattern as finding #5, in two modifiers round 1 didn't
   touch.** `exercise_limitation` and `family_history_context` hardcoded
   a specific claim regardless of what the caution's own `detail` said.
9. **Finding #2's conditional-write fix doesn't cover overlapping
   deliveries or reconciliation.** `RUNNING -> RUNNING` is still allowed,
   so two concurrent SQS deliveries can both invoke the agent; a record
   stuck `RUNNING` after repeated consumer failures has no reconciliation
   against the DLQ.
10. **The stress-harness unification (see the entry below) was itself
    incomplete.** Both async success checks still accepted
    `status == "SUCCEEDED"` alone, without also requiring `safe is True`
    -- disagreeing with the sync path, which already required both.
11. **`run_id_validation.py` missed some of AWS's own documented invalid
    characters**: the surrogate range and the two Unicode noncharacters,
    reachable via a JSON body's `\uXXXX` escapes.
12. **Finding #11's IAM narrowing was itself incomplete.**
    `grant_write_data` still includes `DeleteItem`/`BatchWriteItem`,
    unused by `adapter.py`; the same over-grant pattern was untouched on
    every other DynamoDB-writing handler.
13. **Several documentation claims overstated the post-fix state**:
    `README.md`'s "provably grounded" and "never echoes the question"
    (true for the deterministic narrator's own output construction, not a
    structural guarantee about what reaches an LLM narrator as input,
    since `llm_narrator.py` puts the raw question directly into the
    prompt); a few remaining "guaranteed eventual success" phrases the
    original correction missed; and a CloudWatch `Invocations` count
    presented as proof of Lambda origin when that metric is model-level,
    not caller-level.

**Decision**: Fixed all of 1-5 (the regressions), 7, 8, 10, 11, 12, and 13
directly, each with a new or extended regression test reproducing the
specific failure mode. Did **not** attempt 6 (cross-marker value/unit
binding) or 9 (a processing lease + DLQ reconciliation) in this pass --
both need a real design change (structured claim rendering; a reclaimable
lease with attempt ownership), not a quick patch, and forcing one in
without the same care given to the rest of this project's architecture
would risk the same class of regression this whole review cycle just
caught. Documented both as open backlog items in
`docs/INDEPENDENT_REVIEW_FINDINGS.md`, the same way finding #3's
crash-between-calls gap already was, rather than silently left unrecorded.

**A note on how #8 was fixed, since it introduced its own near-miss**:
rendering the caution's raw `detail` text directly (instead of an assumed
specific claim) means any number literally present in that text --
e.g. the "2" in "type 2 diabetes" -- now appears in the visible answer.
The first version of this fix broke `test_agent_edge_cases.py` and
`test_agent_main_question.py` because that "2" wasn't registered as a
grounded value and `verify_numeric_grounding` correctly flagged it as
ungrounded. Fixed by extracting any numbers present in the caution's own
`detail` into the corresponding `GroundedFact.numeric_values` -- they're
sourced from real reported data, not narrator invention, so they should
be grounded, just like any other sourced value. Noted here because it's
exactly the kind of self-inflicted regression this whole review cycle is
about, caught by running the full test suite before considering the fix
done rather than only the specific new test written for it.

**Verification**: Full kernel suite (141 tests, up from 135) and full
infra suite (129 tests, up from 125) both pass; `ruff`/`ruff format`/
`mypy` clean on both; `cdk synth --all` succeeds with the new IAM grants.
The three originally-failing live questions (LDL trend, HbA1c trend,
eGFR) were re-run against the real pipeline after the fix and now return
`safe=True`. The `states:DescribeExecution` grant and the per-handler IAM
narrowing were verified against the real synthesized CloudFormation
template, not assumed from the CDK call alone. No new `cdk deploy` was
made in this pass -- unlike the two rounds before it, these fixes were
verified against synthesized templates and moto-mocked/kernel tests, not
re-deployed and re-exercised against the live account. That's a smaller
verification bar than the first two rounds held themselves to, disclosed
here rather than implied otherwise.

---

## 2026-09-05 — `stress_test.py`'s success definition was inconsistent across commands; unified

**Context**: An independent review pointed out that `burst-async`'s
success check (`desc["status"] == "SUCCEEDED"`, the raw Step Functions
execution status) can disagree with what actually happened to the agent
run. `RecordFailure`/`RecordTimeout` are both plain `End: true` states
reached via `InvokeAgent`'s `Catch` branch, not an unhandled execution
error -- so a run where the agent genuinely failed, and the state machine
correctly caught and recorded that failure, still reports its own
top-level execution status as `SUCCEEDED` (exactly right for "did the
*workflow* complete as designed," wrong for "did the *agent's answer*
succeed," which is what this harness's `ok` field is supposed to mean).
`burst-queue` already checked the DynamoDB application-level status
instead -- meaning the two async paths weren't even measuring the same
thing when compared against each other in `docs/STRESS_TEST.md`.
Separately, `burst-sync`/`adversarial` (`_invoke_ask_handler`) counted
HTTP 200 alone as success, not also requiring `safe: true` from the
response body.

**Decision**: `_start_and_poll_execution` (`burst-async`) now polls the
DynamoDB record directly, the same approach `_enqueue_and_poll`
(`burst-queue`) already used, so both async paths share one definition.
`_invoke_ask_handler` (`burst-sync`, `adversarial`) now requires
`status == 200 and safe is True`. Did not extend this to a full
transport/workflow/application/safety-level breakdown in the reporting
(the review's fuller suggestion) -- unifying the single `ok` definition
across commands was the load-bearing gap; splitting it into multiple
reported dimensions is a further, separate improvement not done here.

**Verification**: re-ran `burst-async -n 5` live against the real
deployed state machine after the fix -- 5/5, with the harness's own
output now correctly showing the DynamoDB-sourced `narrator_backend`
(`"bedrock"`) and `safe` (`true`) fields, which the old
execution-status-only check never surfaced at all. Checked whether this
retroactively changes any previously-published `STRESS_TEST.md` numbers:
no -- every failure observed in that pass was `Lambda.TooManyRequestsException`
at the `MarkRunning` step, which has no `Catch` and so fails the
*execution* outright (not caught and gracefully recorded) -- meaning the
old and new checks agree for every run actually measured. The bug was
real, but it happened not to distort the specific numbers already
published; it would have mattered for any run where the agent's own
logic (not Lambda-service throttling) was what failed.

---

## 2026-09-05 — Switched `get_dev_token.py` from the ID token to the access token; the original ADR's technical claim was wrong

**Context**: An earlier decision (below, "App Client is a public client...
ID token not access token") chose the ID token specifically because
"the access token carries a `client_id` claim instead and isn't the
conventional shape for this check." An independent review flagged this
as factually incorrect: API Gateway's HTTP API JWT authorizer checks the
configured audience list against the token's `aud` claim when present,
and automatically falls back to checking `client_id` when it isn't --
exactly the shape a Cognito access token has. This is documented AWS
behavior, not an assumption. Access tokens (meant to carry scopes
authorizing API calls) are also the more conventional OAuth2 choice for
this purpose than ID tokens (meant to represent user identity to the
client application that requested them, not to authorize a downstream
API) -- the original choice was backwards from OAuth2 convention on top
of resting on an incorrect technical premise.

**Decision**: `get_dev_token.py` now exports `CARE_AGENT_ACCESS_TOKEN`
(the OAuth2 access token) instead of `CARE_AGENT_ID_TOKEN`. **No change
was needed to `api_stack.py`'s `HttpJwtAuthorizer` configuration** --
`jwt_audience=[app_client.user_pool_client_id]` already works for both
token shapes, since the authorizer itself handles the `aud`-vs-`client_id`
fallback. This is a real example of a fix that turned out to be far
smaller in scope than the finding suggested: correcting the actual
premise showed the "bug" was entirely in which token a client chose to
send, not in how the deployed infrastructure validates it.

**Not done**: this switch does not add per-route OAuth scopes (e.g. a
custom Cognito resource server with `runs.read`/`runs.write`-style
scopes, enforced via `authorizationScopes` on each HTTP API route). The
independent review's finding was specifically about the *token type*
being suboptimal, not about the *absence* of scope-based route
authorization -- today, any successfully authenticated caller (regardless
of token type) can call any route; ownership is enforced at the
application/data layer (`owner_sub`, see the authorization-vulnerability
fix elsewhere in this log), not via OAuth scopes. Adding real per-route
scopes would be a legitimate further improvement, not done here.

**Verification**: `tests/test_get_dev_token.py` (PKCE math, URL/request
construction) is unaffected -- it never touched the token-exchange
response shape. `infra/tests/test_live_endpoint_smoke.py` renamed its
env var accordingly. The actual live login flow (an interactive browser
step through the real Cognito Hosted UI) needs to be re-run once by
whoever next uses `get_dev_token.py` to confirm the resulting access
token is accepted end-to-end against the deployed API -- that step needs
a human at a real browser and wasn't done as part of this fix.

---

## 2026-09-05 — Independent review found a real authorization vulnerability and a run-record data-integrity bug; fixed both

**Context**: Per the challenge brief's own process checklist ("get a second,
independent AI session to critically review the phase"), an independent
model reviewed this repository's code (not just its docs) with explicit
instructions to verify claims against implementation rather than take the
documentation at face value. It found, and reproduced, two High-severity
issues -- verified directly against this repo's own code before accepting
either as real (see the specific verification steps below, not just
"the reviewer said so"). Full findings list, including the ones not
addressed here: `docs/INDEPENDENT_REVIEW_FINDINGS.md`.

**Finding #1 -- authentication existed, authorization didn't.** API
Gateway's Cognito JWT authorizer (Phase 2) validates that a request
carries a genuine, unexpired token -- but nothing downstream of that ever
checked *which* run records the token's holder was entitled to touch.
`get_run.py` and `cancel_run.py` looked up/mutated records by `run_id`
alone; any authenticated caller who knew or guessed a `run_id` could read
or cancel any other caller's run. Verified directly: read both handlers'
source, confirmed neither referenced the request's JWT claims at all.

**Finding #2 -- three paths sharing one run_id keyspace, zero conditional
writes outside the Step Functions leaf.** `/ask` (`adapter.py`), `/runs`
(`mark_running.py`), and `/jobs` (`enqueue_job.py`/`process_job.py`) all
key DynamoDB by `run_id`, but only `record_result.py`'s terminal write was
ever conditional. `adapter.py`'s `put_item` was a full, unconditional
overwrite -- a `/ask` call reusing another path's `run_id` would silently
erase that record's `status` entirely (`put_item` replaces the whole item,
it doesn't merge fields). `process_job.py`'s writes were unconditional
too: SQS's at-least-once delivery means a redelivered message (the first
attempt's Lambda timed out, or its ack didn't land before the visibility
timeout) could set an already-`SUCCEEDED` record back to `RUNNING` and
re-run the agent, and `cancel_run.py` marking a queued job `CANCELLED`
could get silently overwritten the moment `process_job.py`'s own write
landed, since neither side checked the other. Verified directly: traced
every DynamoDB write across `adapter.py`, `mark_running.py`,
`enqueue_job.py`, and `process_job.py`; none but `record_result.py` used
`ConditionExpression`.

**Decision**: Redesigned the run record's write contract instead of
patching each symptom individually:

- Every record now carries `owner_sub` (the creating caller's Cognito
  `sub` -- the actual authorization principal; `user_id`, by contrast, is
  a caller-supplied field naming whose *synthetic health-data profile* a
  question is about, and was never itself proof of identity) and
  `execution_type` (`SYNC` / `STEP_FUNCTIONS` / `SQS`).
- New `auth_context.py` extracts `owner_sub` from
  `event.requestContext.authorizer.jwt.claims.sub` -- the claims HTTP
  API's JWT authorizer attaches to the event, already present on every
  route (all of them require the authorizer), just never read by any
  handler.
- `get_run.py` now returns 404 -- not the data, not a 403 -- for a
  non-owner. 404 rather than 403 is deliberate: confirming "this run
  exists but isn't yours" is itself information a non-owner shouldn't be
  able to learn.
- `cancel_run.py`'s conditional update now folds the ownership check
  *into the same atomic `ConditionExpression`* as the status check
  (`owner_sub = :owner_sub AND (#status = :queued OR #status = :running)`)
  rather than a separate get-then-act -- closing the TOCTOU race a
  separate check would leave open. It also now only attempts
  `stop_execution` when `execution_type == "STEP_FUNCTIONS"` (read back
  via `ReturnValues=ALL_NEW` on the same call, no extra read) -- it used
  to call `stop_execution` unconditionally and silently swallow the
  resulting `ExecutionDoesNotExist` for an SQS-queued job via a bare
  `except ClientError: pass`, reporting success while `process_job.py`
  was about to overwrite the "cancelled" record anyway.
- `adapter.py`, `mark_running.py`, and `enqueue_job.py` now create their
  record with `ConditionExpression: attribute_not_exists(run_id)` --
  cross-path `run_id` collision now returns 409 instead of silently
  replacing another path's record. `adapter.py` also now transitions
  through `RUNNING` -> `SUCCEEDED`/`FAILED` instead of writing nothing
  until the very end, so a request that fails partway doesn't leave no
  record at all.
- `process_job.py`'s writes are now conditional on the record's *current*
  status: the initial `RUNNING` write only proceeds from `QUEUED` or
  `RUNNING` (idempotent under redelivery, but refuses to reopen a
  terminal record); the final write only proceeds from `RUNNING`
  (mirroring `record_result.py`'s existing, already-correct pattern). A
  failed condition is treated as "something else already finalized this
  run" -- not an error, and specifically not re-raised, since re-raising
  would just cost the queue another wasted redelivery attempt on work
  that's already settled.

**Verification**: reproduced both original bugs against this repo's own
code before fixing them (a fabricated second-caller JWT could read/cancel
a first caller's run; a simulated cancel-during-processing race left a
`SUCCEEDED` overwrite), then added regression tests that reproduce the
exact same scenarios and assert the fix holds --
`test_get_run_owned_by_another_caller_returns_404_not_403`,
`test_cancel_run_owned_by_another_caller_returns_404_not_the_real_status`,
`test_cancel_run_cancels_a_queued_sqs_job_without_attempting_stop_execution`,
`test_mark_running_refuses_to_overwrite_a_run_id_collision`,
`test_enqueue_refuses_to_overwrite_a_run_id_collision`,
`test_process_job_does_not_reopen_or_reprocess_an_already_cancelled_job`,
`test_process_job_final_write_does_not_clobber_a_cancellation_that_raced_in_mid_processing`.
Full kernel + infra suites re-run clean after the change (see the
adjacent commit).

**Consequence**: This is the single most important fix this project has
made outside of getting a feature working -- an authorization gap in a
health-data application, even over synthetic data, directly contradicts
the project's own stated safety-first framing, and it existed through
four completed phases and a full stress-testing pass without being
caught, because none of that testing ever asked "what happens if a
*different* authenticated caller tries this." That question is exactly
what an independent second reviewer is for.

---

## 2026-09-05 — Independent review found the numeric-grounding and diagnosis/dosing safety checks had real, exploitable gaps; tightened all four

**Context**: Same independent review as above. It constructed six
narrator outputs and ran them through `safety.run_safety_checks` directly
-- not claiming a real Bedrock call produced them, just showing the
*validators themselves* would pass each one as `safe=True`. All six were
reproduced against this repo's actual code before any fix:

| Probe | What it proved |
|---|---|
| `"Your HbA1c is 162%."` | `verify_numeric_grounding` only checked "does this number appear *somewhere* in the grounded facts," never *which marker* it's attached to -- a real, correctly-grounded LDL-C value (162 mg/dL) could be reattached to a completely different marker and unit. |
| `"Your LDL-C is 5 mg/dL."` | `allowed_extra_numbers` (the ordinal-list-marker exemption, hardcoded to `{1.0, ..., 5.0}`) exempted those *values* anywhere in the text, not just at the position they're actually safe (a line-leading "5. " list marker) -- so a fabricated value happening to equal a valid list-numbering value passed everywhere. |
| `"Your LDL-C is 999mg/dL."` | `_NUMBER_RE`'s trailing `(?![\w.])` blocked matching a number immediately followed by a letter -- any fabricated value could escape numeric extraction *entirely* just by omitting the space before its unit. |
| `"Diabetes is your confirmed condition."` | `_DIAGNOSIS_PATTERNS` only matched "you have/are diagnosed with X" phrasings, not "X is your condition." |
| `"Swallow one vitamin D capsule every morning."` | `_DOSING_PATTERNS` required a digit (mg/mcg/IU/etc.); a written-word dosing/frequency instruction with no digits at all matched none of them. |
| `""` (empty string) | No check verified the answer contained anything -- an empty answer trivially passes every check (no diagnosis pattern matches nothing, no number to be ungrounded). |

**Decision**: Rewrote `safety.py`'s numeric-grounding check around a new
capability rather than only patching each regex individually:

- `GroundedFact` gained an optional `unit` field (`models.py`), populated
  *directly from the source biomarker's own `unit` field* at construction
  time in `agent.py` -- not parsed back out of the `claim` string's free
  text, which would just move the fragility rather than remove it.
- `verify_numeric_grounding` now checks (value, unit) pairs for any
  number with a recognized unit immediately adjacent (`_KNOWN_UNITS` --
  the *actual, complete* unit vocabulary this project's sample data uses,
  verified by scanning every marker in `data/sample_bloodwork.json`, not
  guessed): the number must match a grounded fact carrying that *same*
  unit, not just the same value attached to any marker. A number with no
  recognized unit adjacent (or one using a unit this project doesn't yet
  recognize) still falls back to the older, weaker value-only check --
  there's no way to bind context that isn't there.
- The ordinal-list-marker exemption is no longer a value-based allowlist.
  It's now a position-based one: only the exact character span of a
  line-leading `N. ` marker is exempted, via `_ORDINAL_LIST_MARKER_RE`,
  not the numeral's value anywhere else in the text. `_ORDINAL_NUMBERS`
  and the `allowed_extra_numbers` parameter it fed were removed from
  `agent.py`/`safety.py` entirely -- the position-based mechanism doesn't
  need a caller-supplied allowlist at all.
- `_NUMBER_RE`'s trailing lookahead was removed (the leading
  `(?<![\w.])` -- which correctly excludes digits embedded in identifiers
  like `kb_a1c_006` -- was kept). A number glued to its unit with no space
  is now extracted correctly either way.
- `_DIAGNOSIS_PATTERNS` and `_DOSING_PATTERNS` were both expanded to catch
  the specific phrasings above plus direct siblings (`"your condition
  is X"`, `swallow/take one <capsule/tablet/pill/...>`, a dosage-form word
  within ~40 characters of a frequency word like "every morning").
- Added a fourth check, `check_non_empty`, run as part of
  `run_safety_checks`.

**What this does *not* claim** (see `safety.py`'s own module docstring,
rewritten to say this explicitly): checks 2 and 3 remain pattern-based
over English phrasing and cannot be made complete against a sufficiently
creative paraphrase -- expanding pattern coverage raises the bar, it
doesn't close the class of bypass. Check 4's value+unit binding only
applies to units in `_KNOWN_UNITS`; a number attached to an unrecognized
unit spelling still only gets the weaker check. None of this replaces
`agent.py`'s existing fallback-to-mock-narrator behavior on any check
failure, which remains the actual safety net -- these checks are what
that fallback depends on being accurate.

**Verification**: all six original probes re-run against this repo's
actual pipeline (`HealthAgent.ask()`'s real grounded facts, not
hand-constructed fixtures) after the fix -- all six now correctly fail
the check that used to let them through. 8 new regression tests in
`tests/test_safety.py`, one per probe plus the two regex bugs the fix
itself introduced and caught before committing (an ordinal-marker span
mismatch between the exemption regex and `_NUMBER_RE`'s own match
boundaries, and a `\b`-after-`%`-before-punctuation edge case in the new
value-unit regex -- both caught by running the new tests, not assumed
correct on the first attempt). Full kernel suite (132 tests) and
`care-agent eval-samples` re-run clean after the change.

**Consequence**: This is the second-most-important fix from the same
review, and arguably the more sobering one: these checks are the
project's stated reason an LLM narrator is safe to use at all
("`kb_grounding_002`... is what makes an optional LLM narration pass safe
to use"), and every prior phase's real-Bedrock evidence
(`docs/PHASE4_BEDROCK_EVIDENCE.md`) happened to never trigger any of
these six specific gaps -- not because the gaps weren't there, but
because the real model's actual phrasing choices didn't happen to hit
them in the handful of live calls made. Passing live evidence and passing
an adversarial review are different bars; this project had only cleared
the first one.

---

## 2026-09-05 — Two previously-published stress-test claims were wrong; corrected

**Context**: Same independent review. Two of its findings weren't about
the application's behavior at all, but about whether this project's own
*measurements* of that behavior were trustworthy.

**#6 -- `stress_test.py --no-retry` didn't actually disable retries.**
`Config(retries={"max_attempts": 1})` was meant to show what a real API
Gateway caller (no SDK retry safety net) experiences under Lambda
throttling. Checked directly against `client.meta.config.retries`: it
resolves to `total_max_attempts: 2` -- one retry still happens, despite
the name. `total_max_attempts=1` (with `mode="standard"`) is the actual
zero-retry setting, confirmed the same way. **Consequence**: the
originally published `docs/STRESS_TEST.md` "burst-sync, SDK retry
disabled: 10/15 ok" result was measured with one retry still active, not
zero. Fixed the harness and re-ran the affected comparison -- see
`docs/STRESS_TEST.md` for the corrected number.

**#9 -- the "3 retry attempts" description of the Step Functions retry
policy was incomplete.** Synthesizing `OrchestrationStack`'s actual ASL
showed CDK inserts its *own* default retry policy
(`Lambda.ClientExecutionTimeoutException`/`ServiceException`/
`AWSLambdaException`/`SdkClientException`, 6 attempts) onto every
`LambdaInvoke` task automatically, ahead of the custom 3-attempt policy
this project added -- something the code never disabled and the docs
never mentioned. Step Functions resolves overlapping `Retry` entries by
taking the *first* one in the array whose `ErrorEquals` list contains the
specific error that occurred, not by summing or always using the first
entry -- so for `Lambda.TooManyRequestsException` specifically (the only
error type actually observed throughout this project's stress testing,
and the only one of the four error codes *not* also in CDK's default
policy), the custom 3-attempt policy is genuinely what governed, and the
previously-published throttling numbers are unaffected. But the blanket
claim that every `LambdaInvoke` task retries "3 times" was inaccurate for
the other three error codes, which would get CDK's 6-attempt default
instead. Corrected `orchestration_stack.py`'s module docstring and
`docs/STRESS_TEST.md`/`AWS_ROADMAP.md`'s phrasing to describe both
policies rather than only the one this project added on purpose.

**Consequence**: Neither of these changes the conclusions already drawn
(the sync path still has meaningfully less resilience than the async
paths; the async retry fix from the earlier stress-test still measurably
helped) -- but both are a reminder that a stress-testing tool's own
configuration is exactly as susceptible to being wrong as the thing it's
testing, and deserves the same "verify, don't assume" treatment.

---

## 2026-09-04 — Added an SQS-buffered path as a direct, load-tested comparison against Step Functions retry

**Context**: After the concurrency stress test found Step Functions'
bounded retry degrading under enough sustained load (41/50 succeeding at
a 50-concurrent burst -- see `docs/STRESS_TEST.md`), the user asked
specifically whether adding a real SQS-buffered path -- matching the
pattern the Azure/Durable-Functions counterpart uses internally -- would
improve on that, and asked for it to be built and load-tested, not just
discussed.

**Decision**: Built `QueueStack` (`infra/stacks/queue_stack.py`) as a
genuinely separate, deployed third path, not a modification of the
existing two: `POST /jobs` (`enqueue_job.py`) writes a `QUEUED` record and
sends one SQS message, returning 202 immediately; an SQS-triggered Lambda
(`process_job.py`) consumes messages with the event source's
`max_concurrency=5` -- a hard cap on concurrent consumer Lambdas
*regardless of queue depth*, which is the mechanism this whole comparison
is about. A dead-letter queue (`maxReceiveCount=3`) catches genuinely
un-processable messages. Deliberately reused the existing `RunsTable` and
`GET /runs/{run_id}` (`get_run.py`, already schema-agnostic) for polling
rather than adding a new table or endpoint -- the comparison is about the
ingestion/processing mechanism, not about needing a parallel data model.

**Why `max_concurrency=5`, not higher**: the account's real Lambda
concurrency ceiling is 10, shared across every function. Setting the
queue's consumer cap to half of that leaves headroom for every other
Lambda in the account (the sync path, the Step Functions path, the
enqueue Lambda itself) to keep functioning normally while the queue is
actively draining a burst -- capping it at or near 10 would let a large
queue burst starve the rest of the system, defeating part of the point of
buffering in the first place.

**Verification**: ran the identical burst sizes used for the Step
Functions comparison (15, 50) plus a further push to 100 (2x the size
that made Step Functions degrade) against the real deployed queue.
Result: **100% success at every size tested, including 100 concurrent
(10x the account's raw Lambda ceiling)**, at the cost of latency scaling
roughly linearly with burst size (p50 ~13s at 15 concurrent -> ~60s at
100 concurrent) -- exactly the trade-off basic queueing theory predicts
for a fixed-concurrency consumer. `JobsDLQ` stayed empty at every size,
confirmed via `aws sqs get-queue-attributes`, not assumed. Full numbers
and the head-to-head table: `docs/STRESS_TEST.md`.

**Consequence, and the actual comparison point**: this is not "SQS is
better" -- it's a genuine trade-off, now quantified instead of asserted.
Step Functions' retry is faster in the common case and simpler (no extra
queue resource, no DLQ to monitor) but has a bounded retry budget that a
large enough burst can exhaust. SQS buffering has no such ceiling on
eventual success but makes callers wait proportionally longer during a
real burst, and adds real operational surface (a queue + a DLQ to watch).
Which one is "right" depends entirely on whether the caller needs a
bounded-time answer or needs a guaranteed-eventual one -- the same
question the Azure/Durable-Functions comparison was gesturing at, now
answered with real numbers from both sides of this project rather than
architectural intuition alone.

---

## 2026-09-04 — `process_job.py`'s generic DynamoDB writer hit `status` being a reserved keyword

**Context**: While writing `process_job.py` (the SQS consumer, see the
entry above), its `_write_result(run_id, **fields)` helper built an
`UpdateExpression` directly from keyword-argument names (`f"{key} = :{key}"`)
for brevity, unlike `record_result.py`/`mark_running.py`/`cancel_run.py`,
which all hand-write `ExpressionAttributeNames` for `status` specifically.
The very first test run against moto failed with a real
`ValidationException: Attribute name is a reserved keyword; reserved
keyword: status` -- moto correctly reproduces this specific DynamoDB
behavior, so this would have failed identically against the real service.

**Decision**: Fixed by aliasing *every* field name through
`ExpressionAttributeNames` unconditionally (`f"#{key}"` for every key,
not just `status`), rather than special-casing the one reserved word
known today. DynamoDB has a long, non-obvious reserved-word list; a
generic helper that takes arbitrary field names should not have to be
kept in sync with that list by hand every time a new field gets added.

**Consequence**: A small, cheap catch from actually running the test
suite against moto (which models real DynamoDB validation behavior,
not just a generic key-value store) rather than only reasoning about the
code -- exactly the value moto's fidelity is supposed to provide, working
as intended here.

---

## 2026-09-04 — Stress test found two real bugs: truthiness-only input validation, and retry only wired onto one of four Lambda tasks

**Context**: With Phase 4 closed (Bedrock genuinely running in the
deployed Lambdas), the user asked directly whether the runtime was now
"actually" cloud-native end to end, and separately whether it was worth
deliberately stress-testing it -- adversarial input, real concurrency,
robustness, persistence -- rather than assuming it would hold up because
it worked in ones-of-calls testing. Built
`infra/scripts/stress_test.py`, a live (not CI) harness with four
subcommands, and ran all four against the real deployed account. Full
methodology and numbers in `docs/STRESS_TEST.md`; this entry is just the
two bugs it found and why they were fixed the way they were.

**Bug 1 -- non-string `question`/`user_id`/`run_id` produced a 500 or an
uncaught error, not a 400**: `adapter.py` and `start_run.py` both
validated presence with `if not user_id or not question`, which is
*truthy*-only. A number, list, or dict all pass that check. Locally
probing `HealthAgent.ask()` directly with a non-string `question_text`
showed it raises an unhandled `AttributeError` deep inside intent
classification (`.lower()` on a non-`str`) -- in `adapter.py` this was
caught by a broad `except Exception` and turned into a 500 that leaked
the raw Python exception message to the caller; in `start_run.py` it was
worse, since a non-string `run_id` reaches `start_execution(name=run_id,
...)` (which requires a string) with no validation and no catch beyond
`ExecutionAlreadyExists`, surfacing as a raw, uncaught boto3
`ClientError`.

**Decision**: Added an explicit `isinstance(..., str)` check alongside
the existing truthiness check, in both handlers, for all three fields.
Wrong type is the caller's mistake (400), not an internal failure (500) --
same reasoning already applied to the `isinstance(body, dict)` fix from
Phase 1's null-JSON-body bug. Did *not* add the same check inside
`agent_task.py` (the Step Functions task Lambda) -- its docstring already
documents a deliberate design choice to let exceptions propagate so Step
Functions' own `Catch` becomes the error boundary for that path; a
non-string `question` reaching it (which can now only happen via a
direct, bypassing-the-API invocation, not through `start_run.py` anymore)
still correctly lands the execution in `FAILED`, just not as cheaply as
rejecting it at the API boundary.

**Verification**: added 16 new tests across `test_adapter.py` (type
checks + a 7-case adversarial-input sweep: empty, whitespace, 50k chars,
multilingual Unicode, control characters, SQL-injection-shaped,
prompt-injection-shaped) and `test_orchestration_lambdas.py` (the
`start_run.py` equivalents); all run in CI going forward, all against the
mock narrator (free, deterministic -- the mock is template-based and
structurally can't be talked into anything, so these test input-handling
robustness, not LLM safety). Redeployed and re-verified live against the
actual deployed `AskHandler`: a real `aws lambda invoke` with
`question: 12345` now returns a clean 400 with no leaked exception text.

---

## 2026-09-04 — Step Functions retry was only wired onto InvokeAgent; a live burst test found the other three tasks equally exposed

**Context**: Part of the same stress-testing pass. Fired 15 concurrent
Step Functions executions at the deployed state machine (`stress_test.py
burst-async -n 15`) to compare the async path's resilience against the
synchronous `/ask` path under the same load. Expected the async path,
with Phase 3's "bounded retry" as one of its stated reliability
properties, to clearly outperform the sync path (which has no retry at
all). Instead: **10/15 succeeded, 5 failed -- the same failure count as
the unprotected sync path**, which defeated the point of having retry at
all.

**Investigation**: pulled the Step Functions execution history for one of
the 5 failures. It died at the *first* state, `MarkRunning`, within
~150ms of `ExecutionStarted` -- a single `TaskFailed`
(`Lambda.TooManyRequestsException`) immediately followed by
`ExecutionFailed`, with no retry attempt visible at all.
`orchestration_stack.py` only ever called `.add_retry(...)` on
`invoke_agent_task`; `mark_running_task`, `record_success_task`,
`record_failure_task`, and `record_timeout_task` had none. The account's
Lambda concurrency ceiling (10 -- see `docs/STRESS_TEST.md` for how this
was confirmed via `aws service-quotas`) is shared across every Lambda
function in the account, so a burst throttles whichever task happens to
be invoking at that moment with equal likelihood -- not just
`InvokeAgent`, which is the only one anyone had been watching.

**Decision**: Extracted the retry policy (3 attempts, 2s interval, 2x
backoff, the same `Lambda.*`/`TooManyRequestsException` error list
already used for `InvokeAgent`) into one shared
`_add_throttling_retry()` helper and applied it to *all four*
`LambdaInvoke` tasks in the state machine, not just `InvokeAgent`.
Considered widening `MarkRunning`'s retry errors to also cover generic
application exceptions, and rejected that -- retrying a genuine
application bug (as opposed to transient Lambda-service throttling) just
delays the same failure and can mask a real problem; the fix is scoped to
exactly the error class that caused this specific incident.

**Verification**: redeployed, re-ran the identical burst (n=15): 15/15
succeeded (up from 10/15), latency p95 dropped from 13.43s to 11.68s.
Pushed further to find where retry alone stops being enough: n=30 also
hit 15/15 -> 30/30 (100%), n=50 dropped to 41/50 (82%), with all 9
failures again `MarkRunning` exhausting its 3 retry attempts under
sustained load -- an honest capacity limit (retry smooths a burst, it
doesn't create capacity that isn't there), not a remaining bug. See
`docs/STRESS_TEST.md` for the full numbers and what a real fix past this
point would look like (a Lambda concurrency increase or an SQS buffer,
neither implemented here).

**Consequence**: This is the concrete, load-tested version of the
architectural claim Phase 3 made in the abstract ("orchestration buys
real resilience over a bare synchronous call") -- and finding it only
*half* true on the first real burst test is exactly the value of actually
running one instead of trusting the design read well. Also a legitimate
comparison point against the Azure/Durable-Functions side: whatever the
equivalent of "did we remember to apply the retry policy to every
activity, not just the one we were testing" turns out to be there.

---

## 2026-09-03 — Bedrock live call blocked by new-account verification, not by IAM or model access

**Context**: Phase 4's stated highest-priority goal was one real,
non-mocked Bedrock call as evidence -- explicitly because the Azure side
never got this far (`CannotDeployDueToLocalRegulations` on model
deployment, subscription-eligibility, never resolved). Attempted the AWS
equivalent: `aws bedrock-runtime converse` against
`anthropic.claude-haiku-4-5-20251001-v1:0`.

**What happened**: `AccessDeniedException: Your account is currently
being verified. Verification normally takes less than 2 hours.` Ruled out
the two more mundane explanations before accepting this at face value:
- **Not an IAM problem** -- `dev-cli` already has `AdministratorAccess`
  (confirmed earlier, Phase 1's bootstrap fix).
- **Not model-specific** -- tried a second, older Anthropic model
  (`claude-3-haiku-20240307-v1:0`) and got a *different* error entirely
  (`ResourceNotFoundException`, a Bedrock model-lifecycle/legacy-model
  restriction, unrelated to account verification). Two different models
  producing two different, unrelated errors rules out "this one model
  needs access requested" as the actual blocker for the first case.
- The AWS account itself (not just the IAM user) was created today, which
  matches AWS's own explanation: new accounts get a temporary anti-fraud
  hold on higher-risk, billable operations like Bedrock model invocation.

**Decision**: Don't chase this further right now -- it's a time-bound
hold, not a configuration problem to solve. Built and fully tested
`bedrock_narrator.py` regardless (mocked at the boto3-client boundary, so
none of that work depends on the account being unblocked). Left the "real
call" roadmap item explicitly open (⬜, not falsely marked done) rather
than treating the code being ready as equivalent to the capability gap
being closed.

**Consequence, and the actual cross-cloud comparison point**: both clouds
hit a real-account provisioning obstacle on their respective model layer,
but the *shape* of the obstacle differs in a way worth naming directly:
Azure's was a **support-ticket-bound eligibility/regulatory block** with
no stated resolution timeline (a case was opened, never resolved during
that project's timeframe); AWS's is a **self-service, time-bound identity
verification hold** with a stated expected resolution window from AWS's
own error message. Whether that difference holds up (i.e., whether this
actually clears in ~2 hours as claimed) is itself part of what this
comparison is for -- worth updating this entry once it's known either way,
rather than assuming the more optimistic framing is correct just because
it sounds better.

**Update (same day, resolved)**: the hold cleared in well under the stated
window -- confirmed by retrying the exact same `aws bedrock-runtime
converse` call from the previous entry with no other change, and it
succeeded. AWS's "self-service, time-bound" framing held up in practice,
unlike Azure's open-ended support-ticket block. See the next two entries
for what came up immediately after the hold cleared.

---

## 2026-09-03 — Bedrock real call needs a cross-region inference profile ID, not the bare model ID

**Context**: Once the account-verification hold cleared, the first real
`converse` call against the bare on-demand model ID
(`anthropic.claude-haiku-4-5-20251001-v1:0`) still failed, with a
different, unrelated error: `ValidationException: Invocation of model ID
anthropic.claude-haiku-4-5-20251001-v1:0 with on-demand throughput isn't
supported.`

**Decision**: Newer Anthropic models on Bedrock are only invocable through
a cross-region inference profile ID (the `us.` prefix), not the bare
on-demand model ID. Switched `BedrockNarrator`'s `DEFAULT_MODEL_ID` to
`us.anthropic.claude-haiku-4-5-20251001-v1:0` and confirmed the same call
succeeds with no other change. Left `BEDROCK_MODEL_ID` overridable via env
var (already was) with a code comment explaining *why* the default carries
the `us.` prefix, so a future model swap doesn't silently reintroduce this
error.

**Consequence**: This is an AWS-specific gotcha with no Azure-side
equivalent surfaced yet (the Azure project never got a real model call
working at all) -- worth keeping as a concrete example of an
undocumented-until-you-hit-it platform quirk for the eventual
cross-cloud writeup.

---

## 2026-09-03 — Live Bedrock output failed `numeric_grounding` on natural-language dates; fixed in the shared system prompt

**Context**: With the inference-profile fix in place, `care-agent
eval-samples --narrator-backend bedrock` ran against real Bedrock for all
three sample questions. `q_missing_context`'s answer silently fell back to
the mock narrator (`narrator_fallback` present in the trace) instead of
using the real Claude Haiku 4.5 output.

**What happened**: Claude Haiku wrote source dates in prose form ("May 6,
2026") instead of the ISO format the grounded facts use
(`2026-05-06`). `safety.py`'s `verify_numeric_grounding` only recognizes
ISO-format dates as a single grounded unit (`_ISO_DATE_RE`); a
prose-rendered date's individual number tokens (`2026`, `6`, `8`) got
checked as standalone numeric claims, didn't match any grounded value
verbatim, and the whole response was correctly rejected as
possibly-ungrounded -- exactly the safety net Phase 4 set out to test,
working as designed against a real model's actual output style, not a
contrived case.

**Decision**: Fixed at the prompt layer, not the safety-check layer:
added one instruction to the shared `SYSTEM_PROMPT`
(`src/care_agent/narrator/_prompt.py`, used by every LLM-backed narrator)
telling the model to keep dates in the exact `YYYY-MM-DD` format from the
source facts rather than writing them out in words. Did not loosen
`verify_numeric_grounding` to also parse prose dates -- the check doing
its job correctly (rejecting a plausible-looking but unverifiable
rewording) is the behavior worth keeping; the fix belongs on the output
side that can be steered, not on relaxing what "grounded" means.

**Verification**: re-ran the same question after the prompt change --
real Bedrock output now uses `2026-05-06` verbatim, `narrator_backend:
"bedrock"` with no `narrator_fallback` entry, all three safety checks
(`no_diagnosis`, `no_dosing`, `numeric_grounding`) pass. Re-ran the full
mocked test suite (125 passed) and all three `eval-samples` questions
live against real Bedrock afterward to confirm the fix generalized past
the one question that surfaced it, not just patched over a single
example. See `docs/PHASE4_BEDROCK_EVIDENCE.md` for the full real
output/trace.

**Consequence**: A second concrete, non-obvious finding from the one
Phase 4 explicitly prioritized "make at least one real call" for -- this
kind of format-drift-vs-grounding-check interaction is exactly the sort
of thing that never shows up against a hand-written mock response and
only surfaces against a real model.

---

## 2026-09-04 — Bedrock wired into the deployed Lambdas, with IAM scoped to the exact routed model ARNs

**Context**: Everything Bedrock-related up to this point was proven from
the local CLI against a broad-access dev IAM profile -- genuinely a real,
non-mocked call, but not yet the actual deployed cloud runtime calling
Bedrock, and not yet under scoped-down IAM. This was the one Phase 4 item
left open, and the user explicitly asked to close it before moving to any
stress-testing work, correctly pointing out that "is the runtime fully
running in the cloud" wasn't true yet while this gap remained.

**Decision**: Added `infra/stacks/bedrock_grant.py`, a small shared
helper (`grant_bedrock_invoke(fn)`) used by both `OrchestrationStack`
(`AgentTaskHandler`, the Step Functions `InvokeAgent` task) and
`ApiStack` (`AskHandler`, the synchronous `/ask` path) -- the two, and
only two, Lambdas that call `HealthAgent.ask()`. Each gets
`CARE_AGENT_NARRATOR_BACKEND=bedrock` in its environment and an IAM
policy statement scoped to `bedrock:InvokeModel` on exactly 4 resource
ARNs.

**Why 4 ARNs, not 1**: A naive scoping to just the inference-profile ARN
(`arn:aws:bedrock:us-east-1:<account>:inference-profile/us.anthropic....`)
looks sufficient but isn't -- cross-region inference profiles route the
actual request to one of several underlying on-demand foundation models
in different regions, and IAM evaluates permission against *both* the
profile resource and whichever underlying foundation-model resource the
request lands on. Didn't assume this from memory: ran
`aws bedrock get-inference-profile --inference-profile-identifier
us.anthropic.claude-haiku-4-5-20251001-v1:0` against the real account
first, which returned the profile's actual 3 routed foundation-model
ARNs (`us-east-1`, `us-east-2`, `us-west-2`). All 4 ARNs (1 profile + 3
models) are hardcoded explicitly in `bedrock_grant.py` -- no wildcard
anywhere in the resource list, verified by
`test_no_iam_policy_uses_wildcard_resource` (an existing regression guard
in both `test_stacks.py` and `test_orchestration_stack.py`) continuing to
pass unchanged.

**Verification against the live account** (all three bypass API
Gateway/Cognito entirely, the same direct-invoke approach used for
Phase 3's live verification -- no browser login needed):
1. Direct `aws lambda invoke` on the deployed `AgentTaskHandler` --
   real Claude Haiku prose returned, `narrator_backend: "bedrock"`,
   `safe: true`.
2. Direct `aws lambda invoke` on the deployed `AskHandler` (API-Gateway
   proxy-event shape) with a supplement/dosing-adjacent question -- real
   Bedrock output, correctly declined to give a specific dose, and the
   resulting DynamoDB item (fetched back by `run_id`, not just trusted
   from the response) shows `narrator_backend: "bedrock"` written by the
   Lambda itself.
3. A real `aws stepfunctions start-execution` against the actual deployed
   state machine, with the same kind of dosing-adjacent adversarial
   question -- `SUCCEEDED` in ~9.5 seconds, comfortably inside the 25-second
   `InvokeAgent` task timeout despite Bedrock's added latency over the
   mock narrator (this was a real open question -- Bedrock's latency
   eating into a timeout budget sized for the near-instant mock path was
   exactly the kind of thing worth actually measuring, not assuming).
4. Cross-checked all three against `CloudWatch`'s `AWS/Bedrock`
   `Invocations` metric for the model: 3 → 6 across exactly these 3 calls,
   re-queried before and after. **Correction (2026-09-05)**: a second
   independent review correctly noted this metric is model-level, not
   caller-level -- it confirms 3 real Bedrock calls happened, not
   specifically that they came from the Lambdas rather than a local
   process using the same account. The actual evidence of Lambda origin is
   that each call was made by invoking the Lambda/state machine directly
   by name, not the CloudWatch count alone.

**Consequence**: This closes Phase 4 completely -- both "at least one
real, non-mocked call" and "scoped IAM in a deployed Lambda" are done and
independently verified, not just code-complete. It also surfaces the
concrete next question the user raised right after this: Bedrock's real
latency (several seconds, not the mock's near-zero) is now a live variable
in the deployed system, worth stress-testing deliberately (concurrent
load, timeout boundaries, throttling behavior) rather than assumed safe
because it worked in ones-of-calls testing. See `docs/AWS_ROADMAP.md` for
what that stress-testing pass should cover.

---

## 2026-09-03 — Live cancel-race test: cancellation lost, and that's informative, not a bug

**Context**: `record_result.py`/`cancel_run.py`'s conditional-write race
was already proven correct via `test_orchestration_lambdas.py` (both
orderings seeded directly, no timing dependency). Deployed live, a real
test fired `cancel_run` immediately after `start_run` for the same
run_id, to see which side actually wins under real network/Lambda timing
rather than a unit test's artificial ordering.

**Observation**: The state machine's own success path won every time
tried. `agent_task`'s work (the mock-narrator path) completes in well
under a second; a cold-started `cancel_run` Lambda's own invoke + a
DynamoDB conditional write round-trip is comparably slow or slower. By the
time `cancel_run` reaches its own conditional write, `record_result` has
usually already claimed `SUCCEEDED`.

**Decision**: Not a bug to fix -- this is the correct, honest behavior of
optimistic concurrency: whoever's write actually lands first wins, and a
task that finishes in ~1 second was never a realistic cancellation target
in the first place. No code change from this entry; it's here so a future
reader (or a comparison against the Azure side's cancellation behavior)
isn't surprised by "cancel didn't seem to do anything" against this
specific fast, synthetic workload.

**Consequence**: This pattern's practical value shows up once a task is
genuinely slow (a real model call taking several seconds to tens of
seconds, external API calls, anything with real latency) -- which is
exactly the situation Phase 4's Bedrock integration will introduce.
Re-testing the cancel race after Phase 4 lands, with a task that actually
takes long enough to plausibly cancel mid-flight, would be a more
meaningful test of this mechanism than repeating today's version.

---

## 2026-09-03 — State machine finalization uses Lambda Tasks, not direct DynamoDB ASL integrations

**Context**: Step Functions can write to DynamoDB two ways: a direct
service integration (`tasks.DynamoUpdateItem`, no Lambda involved) or a
Lambda Task that itself calls `boto3`. The direct-integration route is
"more native" and is what a purist reading of "use Step Functions'
reliability features" might reach for first.

**Decision**: Used Lambda Tasks (`mark_running.py`, `record_result.py`)
for every DynamoDB write instead. Direct ASL integrations require typed
`DynamoAttributeValue` values, and dynamically inserting a *boolean*
(`safe`) sourced from `$.agent_result.safe` into that typed system has no
clean built-in path (`JsonPath.string_at` is for strings; there's no
dynamic-boolean equivalent) — the workaround options were all uglier than
just writing five lines of `boto3` in a Lambda.

**Consequence**: The retry/timeout/catch/choice *orchestration* is still
100% native Step Functions (that's the actual Phase 3 requirement); only
the leaf-level "how does a value get into DynamoDB" step is a thin Lambda
instead of raw ASL. This also made the terminal-state race directly unit
-testable with ordinary `moto` + `boto3` mocking
(`test_orchestration_lambdas.py`), which a pure ASL integration would have
made harder to exercise outside a real deployed state machine.

---

## 2026-09-03 — Execution name = run_id, for a free idempotency property

**Context**: `start_run.py` needed some way to avoid double-starting a run
if a client retries a `POST /runs` call (e.g. after a client-side timeout
that wasn't actually a server failure).

**Decision**: Use `run_id` as the Step Functions execution *name*, not
just data passed in the execution input. For a STANDARD state machine,
starting an execution with a name that's already in use (within Step
Functions' ~90-day execution-history retention) raises
`ExecutionAlreadyExists` rather than starting a second, independent run.
`start_run.py` catches that specific error and treats it as success —
the run is already in flight (or finished); there's nothing new to start.

**Consequence**: This is a *second*, independent idempotency mechanism,
layered on top of (not a replacement for) the DynamoDB conditional-write
terminal-state protection `record_result.py`/`cancel_run.py` implement.
The execution-name check prevents a duplicate *state machine run* from
starting at all; the conditional write protects against races *within* a
single run's lifecycle (e.g. cancel vs. natural completion). Worth noting:
`moto`'s Step Functions mock doesn't actually enforce
`ExecutionAlreadyExists` for duplicate names (verified while writing
tests), so that specific behavior is tested against a directly mocked
boto3 client rather than moto's state-machine simulation.

---

## 2026-09-03 — Cognito's default email never delivered the sign-up code; confirmed via admin API instead

**Context**: `AuthStack`'s User Pool doesn't configure a custom email
sender (no SES integration), so it falls back to `COGNITO_DEFAULT` --
Cognito's own built-in email sending. Three real sign-up attempts through
the Hosted UI (against real Gmail addresses) all stayed stuck in
`UNCONFIRMED`; no verification code email ever arrived (checked spam too).
This is a known limitation of `COGNITO_DEFAULT`: a low daily send quota
and a sender address/reputation that many providers filter as spam --
not something specific to this account or this code.

**Decision**: Rather than set up SES (real domain verification, sending
limits, production-access request -- meaningful scope for what's still a
Phase 2 auth skeleton), confirmed the stuck user directly with
`aws cognito-idp admin-confirm-sign-up`, using admin credentials against a
User Pool this project itself owns and created purely for testing. The
user then signed in normally (password they'd already set) through the
Hosted UI and completed the real PKCE flow end to end.

**Consequence**: Documented as a manual step, not automated -- this is
exactly the kind of thing `AWS_SETUP.md`/`get_dev_token.py`'s "a human has
to click through a real login" boundary already anticipated, just for a
different reason (missing email, not missing browser). If this project
ever needs self-serve sign-up to actually work unattended, SES + a
verified sending domain becomes real, non-optional scope -- worth flagging
explicitly rather than let "add SES" quietly become an assumed given.

---

## 2026-09-03 — Auth enforced at API Gateway, not in the Lambda handler

**Context**: Phase 2 needed to protect `/ask`. One option was to check the
`Authorization` header inside `adapter.py` itself (decode/validate the JWT
in Lambda code).

**Decision**: Use API Gateway's native Cognito JWT authorizer
(`HttpJwtAuthorizer`) on the route instead. A request without a valid token
never invokes the Lambda at all.

**Consequence**: `lambda_src/adapter.py` needed zero changes for Phase 2 —
it still has no idea auth exists. This keeps the handler's own tests
(`test_adapter.py`) entirely about business logic, and keeps auth
concerns testable independently via CloudFormation assertions
(`test_stacks.py`) rather than needing to mock JWT validation inside a
Lambda unit test. It also means a misconfigured/compromised Lambda can't
accidentally skip an auth check that lives in its own code path -- the
check isn't in that code path at all.

---

## 2026-09-03 — App Client is a public client (no secret), ID token not access token

**Context**: Cognito app clients can be "confidential" (have a secret,
suited to a server that can keep it private) or "public" (no secret,
suited to a CLI/native/browser client that can't). `get_dev_token.py` is a
local script with nowhere secure to keep a secret.

**Decision**: `generate_secret=False`, Authorization Code + PKCE flow
(PKCE is specifically the mechanism that makes the public-client,
no-secret case safe against authorization-code interception). The API
Gateway authorizer validates the **ID token**, not the access token --
the ID token's `aud` claim matches the app client ID directly, which is
what `HttpJwtAuthorizer`'s `jwt_audience` check expects for a
Cognito-issued token; the access token carries a `client_id` claim
instead and isn't the conventional shape for this check.

**Consequence**: `get_dev_token.py` exports `CARE_AGENT_ID_TOKEN`, not an
access token. Worth remembering if this is ever compared against how the
Azure side scopes its equivalent (Entra ID access tokens are the more
conventional choice there) -- a concrete example of "same requirement,
different idiomatic answer per platform," which is exactly the kind of
thing this log exists to capture.

---

## 2026-09-03 — Cognito Hosted UI domain prefix is a hardcoded literal

**Context**: Cognito Hosted UI domain prefixes are globally unique across
*all* AWS accounts (they live under `*.auth.<region>.amazoncognito.com`),
not just this one. `cdk synth` also needs to work with no real AWS
credentials at all (CI's fake-account job) -- so the prefix can't be
computed at synth time via a live `sts.get_caller_identity()` call.

**Decision**: `auth_stack.py` defaults `domain_prefix` to the literal
string `"care-agent-470293170577"` (this project's actual account number,
known from having already deployed once), overridable via a constructor
parameter. Tests pass a distinct literal (`"care-agent-test-synth-only"`)
so `cdk synth`-time template generation never depends on any live account
state either.

**Consequence**: Redeploying this stack to a *different* AWS account
requires passing a different `domain_prefix` explicitly (the default would
still technically work — Cognito domain prefixes aren't required to match
the account they're deployed in — but reusing an unrelated account number
as a label would be confusing). Documented here so that's not a surprise.

---

## 2026-09-03 — Phase 1 deployed live; the live URL isn't committed anywhere

**Context**: `cdk deploy --all` succeeded against a real account
(`470293170577`, `us-east-1`); both stacks are live. Phase 1 has no auth by
design (Cognito is Phase 2) -- the `/ask` endpoint is anonymous.

**Decision**: The API URL is not written into any committed file (docs,
tests, scripts). `tests/test_live_endpoint_smoke.py` reads it from
`CARE_AGENT_API_URL`, set manually per-session, not from a checked-in
value. `AWS_ROADMAP.md` documents the one-line `aws cloudformation
describe-stacks` command to fetch it instead.

**Consequence**: Nobody browsing this public repo's history can find and
hit the live anonymous endpoint. The cost exposure from someone finding it
anyway and hammering it is small at this scale (Lambda/DynamoDB/S3 are all
consumption-priced fractions of a cent per request, no Bedrock calls
happen on the default mock-narrator path this endpoint runs), but there's
no reason to make it easier to find than it has to be before Phase 2 adds
real auth.

---

## 2026-09-03 — `cdk bootstrap` failed once: IAM user had no policy attached

**Context**: First `cdk bootstrap` attempt failed:
`AccessDenied: ... dev-cli is not authorized to perform:
cloudformation:DescribeStacks`. The IAM user existed (created per
`AWS_SETUP.md`) but had no policy attached yet -- `AdministratorAccess`
hadn't actually been attached in the console, just planned.

**Decision**: Not a code fix -- confirmed via `aws iam list-attached-user-policies`
that no policy was attached, had the account owner attach
`AdministratorAccess` in the console, re-ran `aws sts get-caller-identity`
to confirm the ARN and account, then retried `cdk bootstrap`, which
succeeded immediately.

**Consequence**: Worth remembering as a first-deploy checklist item on any
new AWS account: `aws iam list-attached-user-policies --user-name <name>`
is a fast way to confirm "did the policy attachment actually take" before
spending time debugging what looks like a CDK/CloudFormation problem but
is actually an IAM console step that didn't happen.

---

## 2026-09-03 — Lambda packaging: plain file copy, no Docker/pip bundling

**Context**: CDK's typical Python Lambda bundling story assumes third-party
dependencies need `pip install --target` inside a Docker container matching
the Lambda runtime. `care_agent`'s default (mock-narrator) path has zero
third-party runtime dependencies — stdlib + `sqlite3` only — and `boto3`
(the only import the Lambda handler itself adds) already ships in the AWS
Lambda Python runtime image.

**Decision**: `infra/build_lambda_asset.py` does a plain `shutil.copytree`
of `care_agent` + `data/` + the handler into one flat staging directory,
which `aws_lambda.Code.from_asset()` zips as-is. No Docker required to
`cdk synth` or `cdk deploy`.

**Consequence**: Simpler, faster synth/deploy, and one fewer moving part to
debug. This breaks the moment any *deployed* Lambda actually needs an
optional narrator backend's SDK (`anthropic`/`openai`/`google-genai`) — at
that point a real bundling step (or a Lambda Layer) becomes necessary. Not
needed yet: Phase 1 only exercises the mock path in the deployed Lambda.

---

## 2026-09-03 — Both stacks use `RemovalPolicy.DESTROY` + S3 auto-delete

**Context**: CDK defaults DynamoDB tables and S3 buckets to `RETAIN` on
stack deletion — the safe default for production data, but it means
`cdk destroy` silently leaves orphaned (billed) resources behind unless you
know to look for them.

**Decision**: Explicit `RemovalPolicy.DESTROY` on both, plus
`auto_delete_objects=True` on the bucket (S3 buckets aren't deletable via
CloudFormation while non-empty otherwise). This is a demo/learning project
holding synthetic data, not a system where accidental data loss on
`cdk destroy` is a real risk.

**Consequence**: `cdk destroy --all` actually tears everything down in one
command — important for the cost-conscious "delete Phase 5's experiment
when done" instruction in `AWS_ROADMAP.md`. Would need revisiting (RETAIN,
point-in-time recovery, deletion protection) if this were ever pointed at
real data.

---

## 2026-09-03 — Found and fixed a real bug via infra unit tests, not just synth

**Context**: While writing `tests/test_adapter.py`, a test asserting the
Lambda handler returns 400 for an empty request body instead sent the
literal JSON value `null` as the body (a plausible real client mistake).
`adapter.py` parsed it successfully (`json.loads("null")` → Python `None`)
and then crashed calling `.get()` on it — an unhandled `AttributeError`
that would have surfaced to a caller as an opaque Lambda platform error,
not a clean 400.

**Decision**: Added an explicit `isinstance(body, dict)` check after
JSON-parsing, before touching any field. Added three regression tests
(`null`, `[]`, and a bare JSON string as the body) alongside the original
malformed-JSON case.

**Consequence**: Direct instance of the process-discipline note in
`AWS_ROADMAP.md`'s checklist — this shipped because a test was written for
an edge case, not because it was manually reasoned about ahead of time.
Worth remembering when evaluating how much confidence to place in code that
*hasn't* had adversarial tests written against it yet (Phases 2–5, still
ahead).

---

## 2026-09-03 — Fresh git history instead of preserving the source repo's

**Context**: `src/care_agent/` started as a copy of an existing private
repo's business logic. That repo's history is fine on its own, but this
repo is public.

**Decision**: Start this repo with a single clean initial commit rather than
`git clone`-ing history forward. No commit message in the source repo
actually names any company, but several early README/docstring *file
contents* did (fixed in this repo's first commit) — publishing that history
would still let anyone browse old diffs and see the original framing.
Starting fresh avoids that entirely, at the cost of losing line-level
`git blame` provenance for code that predates this repo.

**Consequence**: This repo's history begins from a working, tested state,
not from empty. That's a deliberate, disclosed choice, not an attempt to
misrepresent how the code was built.

---

## 2026-09-03 — Package renamed `nuaura_agent` → `care_agent`

**Context**: The source package name and various docstrings referenced a
specific company/hiring context (name, "take-home challenge/assignment"
framing) that doesn't belong in a public, general-purpose repo.

**Decision**: Renamed the package to `care_agent` (short, neutral, reads
naturally as `python -m care_agent ask ...`). Left the synthetic sample
data files' *content* untouched (`sample_bloodwork.json`,
`knowledge_base.jsonl`, etc.) — they're synthetic fixtures with no
copyright or confidentiality concern; some knowledge-base entries still
carry a `source_name` of "Nuaura mock policy" because that's literally
what's in the data, not something worth hand-editing out of a fixture file.
Renamed the narrator-backend env var `NUAURA_NARRATOR_BACKEND` →
`CARE_AGENT_NARRATOR_BACKEND` for the same reason.

**Alternatives considered**: `clinical_agent_core` — rejected only for
being longer with no added clarity.

**Consequence**: Anyone diffing this repo against the original source will
see an import-path-wide rename plus prose edits in ~15 files; the actual
reasoning/safety/retrieval logic is untouched (verified: full test suite,
ruff, and mypy all pass identically before and after the rename).

---

## 2026-09-03 — Kernel starts at its original (simpler) maturity level, not backfilled to match the Azure counterpart

**Context**: The Azure-side counterpart project has gone through
substantially more hardening (multiple ADRs, auth deployment, durable
orchestration reliability semantics, a FinOps audit, many more edge-case
test files) than this kernel has at the point of import.

**Decision**: Import the kernel as-is, at its current maturity, rather than
trying to backfill it to architectural parity with the Azure side before
starting the AWS build-out. The comparison this project is actually after
is the *cloud-native deployment layer* (compute, orchestration, state,
identity, managed-model integration) — not byte-for-byte identical business
logic. The kernel will get hardened *in parallel*, phase by phase, the same
way the Azure side was, and that hardening process is itself one of the
things worth comparing.

**Consequence**: Early roadmap phases here will look "behind" the Azure
side's current state at a glance. That's expected and disclosed, not a
gap to hide.

---

<!-- Template for new entries:

## YYYY-MM-DD — Short decision title

**Context**: What prompted this decision.

**Decision**: What was chosen.

**Alternatives considered**: What else was considered and why it lost.

**Consequence**: What this makes easier/harder going forward.

-->
