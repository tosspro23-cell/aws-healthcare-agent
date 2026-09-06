# AWS Build-Out Roadmap

Phased plan for deploying `src/care_agent/` AWS-native, as the AWS side of a
two-cloud architecture-comparison learning project. Each phase should land
with its own adversarial/edge-case tests (not just a happy-path smoke test)
and an update to [`DECISIONS.md`](DECISIONS.md) recording what was chosen
and why, before moving to the next phase.

Status legend: ⬜ not started · 🔶 in progress · ✅ done

## Phase 0 — Kernel import + desensitization

- ✅ New public repo created, `src/nuaura_agent/` copied over as
  `src/care_agent/` with reasoning/safety/retrieval logic unchanged.
- ✅ Company name, "take-home"/"assignment"/"challenge" framing removed from
  code, docstrings, README, docs; synthetic data files left untouched
  (no copyright concern, just not described as a specific hiring exercise).
- ✅ Full test suite re-verified after the rename (no regressions).

## Phase 1 — Minimal skeleton deployment

- ✅ `cdk init app --language python` under `infra/`, restructured
  (`infra/infra/` → `infra/stacks/`, split into `data_stack.py` +
  `api_stack.py`).
- ✅ `DataStack`: DynamoDB table for run state (partition key `run_id`,
  on-demand billing) + S3 bucket for full JSON execution traces (blocked
  public access, SSE, TLS-enforced).
- ✅ `ApiStack`: API Gateway HTTP API + Lambda integration (`POST /ask`).
- ✅ Lambda handler (`infra/lambda_src/adapter.py`): thin adapter — parses
  the API Gateway event, calls `HealthAgent.ask()`, writes a compact run
  record to DynamoDB + the full trace to S3, serializes the response.
  Zero third-party runtime deps needed (`care_agent`'s default mock path is
  stdlib-only, `boto3` ships in the Lambda runtime) so packaging is a plain
  file copy (`build_lambda_asset.py`), no Docker bundling.
- ✅ No auth yet (anonymous calls allowed) — deliberately deferred to Phase 2
  so the skeleton isn't blocked on auth configuration.
- ✅ `cdk synth` validated locally (no AWS credentials needed for this step;
  confirmed by running it with a fake account/region and no `~/.aws`).
- ✅ Unit tests: `tests/test_stacks.py` (CloudFormation assertions — correct
  partition key, on-demand billing, blocked public access, no wildcard IAM
  resources) + `tests/test_adapter.py` (Lambda handler logic against a
  `moto`-mocked DynamoDB/S3, 18 cases incl. malformed-JSON-body variants
  that surfaced and fixed a real crash — see `DECISIONS.md`).
- ✅ `cdk bootstrap` + `cdk deploy --all` against a real AWS account
  (us-east-1). Both stacks deployed cleanly on the second attempt — the
  first `cdk bootstrap` failed on `cloudformation:DescribeStacks` because
  the IAM user had no policy attached yet; see `DECISIONS.md`.
- ✅ **Phase 1 acceptance criterion met and verified**: a live `curl`/pytest
  request against the deployed endpoint returns byte-for-byte the same
  `answer` text as running `care-agent ask` locally for the same question
  (compared directly, not just spot-checked). `tests/test_live_endpoint_smoke.py`
  passes all 4 cases against the real endpoint. Confirmed the DynamoDB run
  record and S3 evidence object were both actually written (fetched them
  back by `run_id` after the call, not just trusted a 200 response).

**Phase 1: complete.** The live API URL is intentionally not committed to
this repo (see `DECISIONS.md` — Phase 1 has no auth by design, and an
unauthenticated endpoint's URL doesn't belong in a public repo's history
even though the cost exposure from someone finding and hammering it is
small at this scale). Retrieve it yourself with:

```bash
aws cloudformation describe-stacks --stack-name CareAgentApiStack \
    --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text
```

## Phase 2 — Authentication

- ✅ `AuthStack` (`infra/stacks/auth_stack.py`): Cognito User Pool + Hosted
  UI domain + a public (no-secret) App Client, `AllowedOAuthFlows: [code]`
  — the shape Authorization Code + PKCE requires.
- ✅ `ApiStack`'s `/ask` route now has a Cognito JWT authorizer
  (`AuthorizationType: JWT`, audience = the App Client ID, issuer = the
  User Pool). Enforced entirely at API Gateway — zero changes to
  `lambda_src/adapter.py`, since a request without a valid token never
  reaches the Lambda.
- ✅ `infra/scripts/get_dev_token.py`: walks the real Authorization Code +
  PKCE flow (opens the browser to the Hosted UI, catches the redirect with
  a local stdlib HTTP server, exchanges the code for tokens) to get a real
  bearer token for testing.
- ✅ `cdk synth` validated locally for all three stacks together.
- ✅ Unit tests: `tests/test_stacks.py` gained JWT-authorizer and
  public-client assertions (13 cases total now); `tests/test_get_dev_token.py`
  covers the PKCE math and URL/request construction (6 cases) without
  needing a real browser or network call.
- ✅ `tests/test_live_endpoint_smoke.py` updated: no-token and
  garbage-token requests now assert `401` (always run once `CARE_AGENT_API_URL`
  is set); the existing behavioral checks (matches-local-answer,
  400/404 cases) are gated on `CARE_AGENT_ID_TOKEN` additionally, since they
  need a request that actually reaches the Lambda.
- ✅ **Live verification against the deployed endpoint, complete.** Deployed
  all three stacks; confirmed no-token and garbage-token requests both
  return `401` directly with `curl` before any test ran. Cognito's default
  email delivery (`COGNITO_DEFAULT`) never delivered the sign-up
  verification code (known low-quota/spam-filtered behavior — see
  `DECISIONS.md`), so the account was confirmed via
  `admin-confirm-sign-up` instead of waiting on email; after that, a real
  browser login through `get_dev_token.py` produced a real ID token, and
  all 6 `tests/test_live_endpoint_smoke.py` cases passed against the live
  endpoint, including the two behavioral checks that require the token to
  actually reach the Lambda.

**Phase 2: complete.**

Deliberately its own phase, separate from Phase 1, so auth configuration
issues wouldn't have blocked getting the skeleton running end to end first.

## Phase 3 — Step Functions orchestration (reliability semantics)

- ✅ `OrchestrationStack` (`infra/stacks/orchestration_stack.py`): a Step
  Functions state machine implementing start → bounded retry → timeout →
  terminal state, plus an async API surface (`POST /runs`,
  `GET /runs/{run_id}`, `POST /runs/{run_id}/cancel`) alongside the
  existing synchronous `/ask`.
- ✅ **start**: `MarkRunning` (Lambda Task) writes the initial `RUNNING`
  DynamoDB record.
- ✅ **bounded retry**: `InvokeAgent`'s native `add_retry` (3 attempts,
  2s interval, 2x backoff on Lambda service errors) — not a hand-rolled
  loop.
- ✅ **timeout**: `InvokeAgent`'s native per-task `TimeoutSeconds` (25s)
  plus an overall execution timeout (5 min).
- ✅ **cancellation**: `POST /runs/{run_id}/cancel` (`cancel_run.py`) can
  fire at any point while a run is in flight, entirely outside the state
  machine.
- ✅ **terminal-state ownership**: `record_result.py`'s conditional
  DynamoDB write (`ConditionExpression: status = RUNNING`) is the single
  source of truth for who finalized a run. The state machine's own
  success/failure path and the external cancel handler race for it;
  DynamoDB's atomic compare-and-swap decides the winner. Directly tested
  (`test_orchestration_lambdas.py`): both the "state machine wins" and
  "cancel wins" orderings, asserting the loser's write is silently
  rejected rather than corrupting the winner's record.
- ✅ Failure branch further distinguishes `States.Timeout` from other
  errors via a `Choice` state (`RecordTimeout` vs `RecordFailure`), so a
  timed-out run and a genuinely failed run end up in visibly different
  terminal states, not lumped together.
- ✅ `cdk synth` validated locally for all four stacks together, no
  deprecation warnings.
- ✅ Unit tests: `test_orchestration_stack.py` (12 cases — ASL structure:
  retry config, timeout values, catch routing, choice branching, IAM scope)
  + `test_orchestration_lambdas.py` (16 cases — every new Lambda's own
  logic against moto-mocked DynamoDB/Step Functions, including the race).
- ✅ **Live deploy + verification, complete.** All four stacks deployed;
  `POST /runs` / `GET /runs/{run_id}` / `POST /runs/{run_id}/cancel`
  confirmed to require auth (401 without a token, checked directly, same
  as `/ask`). Invoking `start_run`/`get_run`/`cancel_run` directly
  (bypassing API Gateway, so no browser login needed to verify this part)
  against the real account: a real run went `RUNNING → SUCCEEDED` in
  Step Functions and DynamoDB both, with the correct grounded answer;
  `list-executions` on the real state machine shows both real runs by
  name (the execution name = run_id design). A cancel request fired
  immediately after starting a run *lost* the terminal-state race — the
  mock-narrator path finishes in well under a second, faster than the
  cancel Lambda's own cold-start-plus-conditional-write round trip, so
  `cancel_run` correctly detected it lost and returned `409` with the
  real `SUCCEEDED` status rather than lying about a cancellation. The
  *mechanism* was already proven independently via
  `test_orchestration_lambdas.py` (both race orderings, seeded directly);
  this live run is an honest observation about real-world timing, not a
  gap — cancelling a run that finishes in ~1 second was never going to be
  the common case this feature is for.
- ✅ Comparison note in `DECISIONS.md`: state-machine-native
  retry/timeout/catch/choice vs. hand-written Lambda conditional writes
  for the DynamoDB leaf steps, and what the live cancel-race timing
  result implies about when this pattern actually matters (slow/long
  tasks, not fast synchronous ones).

**Phase 3: complete.**

## Phase 4 — Bedrock integration (highest priority)

- ✅ `src/care_agent/narrator/bedrock_narrator.py` — same pluggable
  `Narrator` interface and safety contract as `openai_narrator.py` /
  `google_narrator.py` / `ollama_narrator.py`, calling `bedrock-runtime`'s
  `Converse` API. Authenticates via the standard AWS credential chain
  (no separate API-key env var, unlike the other cloud backends) — see
  `README.md`'s narrator backend table.
- ✅ **Reuses the existing safety pipeline as-is** — no changes to
  `safety.py`. Directly tested: `test_agent_with_bedrock_narrator_passes_through_when_grounded`
  and `test_agent_with_bedrock_narrator_falls_back_when_unsafe`
  (`tests/test_bedrock_narrator.py`), the same full-pipeline pattern
  already used for Ollama — proves the same `no_diagnosis`/`no_dosing`/
  `numeric_grounding` checks, unmodified, correctly pass a faithful
  Bedrock-shaped response through and correctly reject an unsafe one.
- ✅ Mocked unit tests (9 cases): request/response shape (`modelId`,
  `system`, `inferenceConfig.maxTokens`, multi-block content
  concatenation, empty-content defensiveness), model/region resolution
  from env vars vs explicit args. CI never calls a real endpoint
  (`pytest.importorskip("boto3")` skips the whole file when the `bedrock`
  extra isn't installed, which is always true in CI).
- ✅ **Account-verification hold cleared** — resolved same-day, well under
  AWS's stated ~2-hour window. Confirmed by retrying the exact `aws
  bedrock-runtime converse` call from the blocked investigation with no
  other change; it succeeded. See `DECISIONS.md` for the closed-out
  before/after comparison against the Azure side's open-ended block.
- ✅ **At least one real, non-mocked Bedrock call**, with the real output
  and trace recorded as evidence — **done**. Full real
  `python -m care_agent ask ... --narrator-backend bedrock --trace` output
  (real Claude Haiku 4.5 answer + real `bedrock-runtime.Converse`-derived
  trace, `narrator_backend: "bedrock"`, no `narrator_fallback`, all three
  safety checks passed) recorded in
  [`PHASE4_BEDROCK_EVIDENCE.md`](PHASE4_BEDROCK_EVIDENCE.md). This closes
  the one genuine capability gap the challenge brief called out (the
  Azure counterpart never got a real model call working). Two real,
  non-obvious findings came out of making this call for real rather than
  only against mocks — both documented in `DECISIONS.md`:
  - Newer Anthropic models on Bedrock require a cross-region inference
    profile ID (`us.` prefix), not the bare on-demand model ID —
    `DEFAULT_MODEL_ID` updated accordingly.
  - The real model's natural-language date formatting ("May 6, 2026")
    initially tripped `numeric_grounding` and correctly triggered a
    silent fallback to the mock narrator — fixed at the prompt layer
    (`SYSTEM_PROMPT` now specifies ISO date format), not by loosening the
    safety check itself. Verified the fix generalized past the one
    question that surfaced it by re-running all three `eval-samples`
    questions live against real Bedrock afterward.
- ✅ **Bedrock wired into the deployed Lambdas, with scoped IAM — done.**
  `infra/stacks/bedrock_grant.py` grants `bedrock:InvokeModel` on exactly
  4 ARNs (the inference profile + its 3 routed foundation-model ARNs
  across `us-east-1`/`us-east-2`/`us-west-2` — confirmed necessary via
  `aws bedrock get-inference-profile`, not assumed), nothing broader.
  Applied to both Lambdas that call `HealthAgent.ask()`: `AskHandler`
  (sync `/ask`) and `AgentTaskHandler` (the Step Functions `InvokeAgent`
  task), each also getting `CARE_AGENT_NARRATOR_BACKEND=bedrock` in its
  environment. `test_no_iam_policy_uses_wildcard_resource` (already
  existed for both stacks) continues to pass with the new grant in place.
- ✅ **Live cloud verification, complete** — this is the deployed Lambda
  itself calling Bedrock, not the local CLI. Three real calls made
  directly against the deployed resources (no browser/Cognito login
  needed, matching the same bypass-API-Gateway approach used for Phase 3's
  live verification): a direct `AgentTaskHandler` invoke, a direct
  `AskHandler` invoke, and a real `start-execution` through the actual
  Step Functions state machine with a dosing-adjacent adversarial question
  ("what dosages should I take?"). All three: `narrator_backend: "bedrock"`
  in the response/DynamoDB record, `safe: true`, real Claude Haiku prose
  (not the mock's templated bullets), and the dosing question correctly
  refused specific numbers while still answering helpfully. The Step
  Functions run `SUCCEEDED` in ~9.5s, comfortably inside the 25s task
  timeout despite Bedrock's added latency. CloudWatch's `AWS/Bedrock`
  `Invocations` metric for the model went from 3 → 6 across exactly these
  3 calls, confirmed by re-querying before and after. **Correction
  (2026-09-05)**: a second independent review correctly pointed out that
  this metric is model-level, not caller-level -- it confirms 3 real
  Bedrock calls happened on this account, but doesn't by itself
  distinguish a Lambda-originated call from a local CLI call using the
  same account's credentials. The actual evidence these three specific
  calls came from the deployed Lambdas is that each was invoked directly
  by service name (`aws lambda invoke --function-name AgentTaskHandler...`,
  a real Step Functions `start-execution`), not the CloudWatch count on its
  own. Full evidence: [`PHASE4_BEDROCK_EVIDENCE.md`](PHASE4_BEDROCK_EVIDENCE.md).

**Phase 4: complete.** Both the real-call requirement and the
scoped-IAM-in-a-deployed-Lambda requirement are done and independently
verified against the live account.

## Stress test — adversarial input, real concurrency, robustness, persistence

Run after Phase 4 closed, once both the sync and async paths were
genuinely calling Bedrock from the deployed Lambdas. Full writeup, all
numbers, and the exact commands: [`STRESS_TEST.md`](STRESS_TEST.md).
Tooling: `infra/scripts/stress_test.py` (live, manual, real cost -- not
part of CI).

- ✅ **Adversarial / malformed input.** 20 new permanent CI tests (moto,
  free) covering non-string types, empty/huge/Unicode/control-character/
  injection-shaped input; plus a live 8-prompt real-Bedrock
  prompt-injection sweep (8/8 safe, real model refused every dosing/
  diagnosis/jailbreak attempt directly). **Found and fixed a real bug**:
  `adapter.py`/`start_run.py` validated input presence with a
  truthiness-only check, letting a non-string `question`/`user_id`/
  `run_id` reach internal code and surface as a leaky 500 (or, for
  `start_run.py`, an uncaught boto3 error) instead of a clean 400. Fixed,
  redeployed, live-verified.
- ✅ **Real concurrency / capacity.** Checked the account's actual quotas
  first (Lambda: 10 concurrent executions account-wide; Bedrock Haiku 4.5
  cross-region: 50 RPM) rather than guessing. Burst-tested the sync `/ask`
  path (zero built-in resilience -- 10/15 succeed with SDK retry disabled,
  the honest real-caller number) against the async `/runs` path. **Found
  and fixed a second real bug**: Step Functions retry was wired onto only
  `InvokeAgent`, not the other three Lambda tasks, so the async path
  failed identically to the unprotected sync path (10/15) on the first
  burst. Fixed (retry on all 4 tasks), redeployed, re-verified: 15/15 and
  30/30 succeed after the fix; 50 concurrent finds the real limit of what
  retry alone can absorb (41/50, 82%), an honest capacity boundary, not a
  bug.
- ✅ **Persistence / consistency.** Repeated the Phase 3 start-then-cancel
  race 15 times against the now-Bedrock-backed (meaningfully slower) async
  path. 15/15 consistent terminal DynamoDB states, no double-finalization,
  no missing record, in either race direction (cancel won 14/15 this time,
  a flip from Phase 3's single mock-narrator observation where cancel lost
  every time -- confirms the conditional-write mechanism holds correctly
  in both directions, not just the one direction observed before).

- ✅ **SQS-buffered path, built and load-tested head-to-head against Step
  Functions retry.** `QueueStack`: `POST /jobs` -> SQS -> a consumer
  Lambda capped at `max_concurrency=5` regardless of queue depth, plus a
  DLQ. Same burst sizes as the Step Functions comparison, run for real:
  **15/15, 50/50, and 100/100 all succeed (100%)** -- including at 100
  concurrent, 2x the burst size where Step Functions' retry started
  failing (41/50, 82%) -- at the cost of latency scaling roughly linearly
  with burst size (p50 ~13s at 15 concurrent, ~60s at 100). This is a
  genuine trade-off, not a strict improvement: Step Functions is faster
  in the common case and operationally simpler; SQS measurably sustains
  much higher success rates under load (100% at every burst size tested,
  up to 2x the burst where Step Functions' retry alone started failing)
  but makes callers wait longer during a real burst -- not an unconditional
  guarantee at any scale (finite retries, retention, and a shared
  account-wide Lambda concurrency pool still apply; see the correction in
  `docs/STRESS_TEST.md`).
  Both a third real bug (a DynamoDB reserved-keyword issue caught by
  moto's fidelity on the very first test run) and this whole comparison
  are written up in `docs/DECISIONS.md` and `docs/STRESS_TEST.md`.

⬜ Not done: no fix for Bedrock-side throttling specifically (never
actually triggered -- every failure observed was Lambda-side, on either
path); no request to raise the account's Lambda concurrency limit past
10 (discussed explicitly with the user and deliberately deferred -- no
real traffic yet to justify it, and it wouldn't change either
comparison's conclusion, only where the specific numbers land); no
cancellation support for the SQS-buffered path. See `STRESS_TEST.md`'s
closing section for the full reasoning on each.

## Phase 5 — Vector retrieval experiment (optional, lowest priority)

- ⬜ Try vector retrieval over the existing 68-chunk `knowledge_base.jsonl`
  using either pgvector (Aurora Serverless) or OpenSearch Serverless.
- Not because keyword/BM25 retrieval is insufficient at this corpus size —
  it isn't — but as a hands-on learning exercise.
- ⬜ Tear down whatever gets provisioned afterward to avoid ongoing cost.

## Phase 6 — Frontend / Workbench (complete: all three backend paths, full async trace, public hosting)

Everything through Phase 5 was backend-only: real auth existed (Cognito
Hosted UI is a genuine login page), but nothing called it except this
project's own terminal tooling (`curl`, `pytest`, `get_dev_token.py`).
[`frontend/`](../frontend) is a minimal React/Vite Workbench that turns
that into something usable in a browser, deliberately scoped to the
`/ask` path first rather than the whole API surface at once:

- ✅ Runs the real Authorization Code + PKCE flow through an in-browser
  redirect (`frontend/src/auth.ts`) -- not `get_dev_token.py`'s local
  script standing in for one. Required one infra change: API Gateway had
  never had a browser caller before, so `ApiStack`'s `HttpApi` needed
  CORS configured (scoped to exactly `http://localhost:8765`, not `*`),
  deployed and confirmed live via a real preflight request.
- ✅ Calls `POST /ask` with the resulting access token and renders the
  answer plus its full grounding trace (safety checks, grounded facts,
  limitations, retrieved sources) -- `frontend/src/components/TraceView.tsx`.
- ✅ **Live-verified end to end**, not just built: a real browser sign-in
  through the actual Cognito Hosted UI, a real `/ask` call answered by
  the deployed Bedrock-backed Lambda, `safe: true`, all 4 safety checks
  shown passing, 12 grounded facts rendered. Also caught and fixed a real
  bug this way that no unit test would have: React 18 StrictMode's
  deliberate double-effect-invocation in dev exchanged the one-time
  authorization code twice, and the second exchange failed with a real
  `400` from Cognito -- harmless (the first exchange had already
  succeeded) but a genuine race, fixed with a mount-guard ref.
- ✅ **Async paths wired up**: a mode switcher (`Ask` / `Start run (Step
  Functions)` / `Enqueue job (Queue)`) covers all three backend paths from
  one form. The two async modes poll `GET /runs/{run_id}` every second
  until a terminal status, with a `Cancel this run` button while pending
  (`cancel_run.py`, already load-tested and race-verified in
  `docs/STRESS_TEST.md`, now reachable from the UI). Client-side run
  history (`frontend/src/history.ts`, localStorage-backed) lets any past
  run -- sync, Step Functions, or queued -- be revisited by `run_id` after
  a page reload, demonstrating the backend's actual persistence rather
  than an in-memory response. Deliberately *not* a new "list my runs"
  backend endpoint: the runs table's only key is `run_id`, so a real
  server-side history view would need a new GSI + Lambda + route -- a
  separate, larger piece of infra work than wiring up what already
  exists, left for later if it's ever worth doing.
- ✅ **Live-verified**, including a real race condition caught this way:
  `POST /runs` returns as soon as `start_execution` is accepted, *before*
  the state machine's first task has written the DynamoDB record --
  polling immediately produced a real `404`. Fixed by tolerating a
  bounded run of 404s at the start of a poll loop instead of treating the
  first tick as authoritative. The SQS path doesn't have this race
  (`enqueue_job.py` writes its record synchronously before returning
  202), but the fix applies uniformly rather than branching on
  `execution_type`. Cancellation verified via the exact HTTP calls the UI
  makes (a manual click reliably lost the race against Bedrock's ~1-2s
  response time, a UI-testing limitation, not a functional gap): started
  a run, cancelled it immediately, and confirmed the record stayed
  `CANCELLED` -- not overwritten by the agent's own completion -- 3
  seconds later, well past when it would normally have finished.
- ✅ **Markdown rendering**: Bedrock's prose (`**bold**`, numbered lists,
  headings) now renders properly (`react-markdown` -- never executes raw
  HTML, appropriate for text ultimately produced by an LLM completion)
  instead of showing literal markdown syntax.
- ✅ **Full grounding trace for the async paths too**: `agent_task.py` and
  `process_job.py` now persist to the same `{run_id}.json` S3 evidence key
  `adapter.py`'s synchronous path already used, with matching precise IAM
  (`s3:PutObject` on the object prefix only). `get_run.py` opportunistically
  merges that trace in under a `trace` key when one exists. **A second real
  bug found live**: `get_run.py` is deliberately granted only `s3:GetObject`
  (not `s3:ListBucket`, which would let it enumerate every run_id's
  evidence) -- without `ListBucket`, S3 returns `AccessDenied` instead of a
  clean `NoSuchKey`/404 for an object that doesn't exist yet, which
  crashed the whole `GET /runs/{run_id}` response with a 500 the first
  time a poll landed before the evidence was written. Fixed by treating
  any S3 read failure for this best-effort enrichment as "no trace yet"
  rather than propagating -- the DynamoDB record's status/answer, the
  actual source of truth, was never at risk.
- ✅ **Publicly hosted**: `FrontendStack` (S3 + CloudFront, Origin Access
  Control, no public bucket) serves the built Workbench over HTTPS.
  Two-pass deploy (see `app.py`'s docstring): the CloudFront domain isn't
  known until first deploy, then gets registered as a second Cognito
  callback/logout URL and a second CORS origin. The redirect/logout URI is
  now derived from `window.location.origin` rather than hardcoded to
  `localhost:8765`, so the same build works unmodified locally and hosted.
  **Self-sign-up disabled** before making the link public (`AuthStack`)
  -- an account still has to be created with `AdminCreateUser`, not public
  registration, even though the data behind it is entirely synthetic.
  Mobile-responsive pass (flex-wrapping mode tabs, 16px inputs to avoid
  iOS Safari's zoom-on-focus, 44px touch targets, a narrow-viewport media
  query) -- verified on an emulated 375px mobile viewport against the real
  hosted URL, not just locally.

Live-verified on the actual public URL end to end: sign-in through the
real Cognito Hosted UI (no "Sign up" link anymore), a real `/ask` call
with markdown rendering, a Step-Functions run showing its full trace
after the `get_run.py` fix, all confirmed on both desktop and an emulated
mobile viewport.

Deliberately scoped this way rather than building the full surface at
once, per the same "ship the core loop, verify it live, then expand"
pattern the backend phases used. See [`frontend/README.md`](../frontend/README.md)
for setup and the reasoning behind each scoping choice.

Flagged as worth doing because the Azure counterpart already has a
React/Vite "Workbench" doing exactly this, and a frontend is the natural
comparison point once the AWS-side backend phases are hardened: same
underlying API, is the client-side auth/UX story simpler or harder to
build against API Gateway + Cognito than against Azure Functions +
Entra/MSAL?

## Process checklist (apply at every phase, not just once)

- [x] Adversarial/boundary tests added for the new surface (extreme values,
  empty input, injection attempts, all-normal-data cases) — not only a
  happy-path smoke test.
- [x] `DECISIONS.md` updated with what was chosen and why.
- [x] Self-review pass: does any comment or doc describe a safety check as
  stronger than it actually is? (E.g. "prevents hallucination" when it only
  proves a number has *some* source, not that it's the *right* source; or a
  policy written for one marker class getting applied to all markers by
  accident.) Check this explicitly before calling a phase "done." —
  **this specific self-review pass turned out to be too weak to catch
  what the independent review below found on its own; the item below is
  what actually closed this gap.**
- [x] Get a second, independent AI session (not the one that wrote the code)
  to critically review the phase before moving on. — **Done 2026-09-05**,
  after Phases 0–4 and the stress-test pass, not per-phase as originally
  scoped here (this checklist item went unactioned through every earlier
  phase — worth naming directly rather than quietly backfilling a
  checkmark). Found 15 issues ranging High to Low severity: a real
  authorization vulnerability (any authenticated caller could read/cancel
  any other caller's run), exploitable gaps in the numeric-grounding/
  diagnosis/dosing safety checks, a non-atomic queue write, incorrect
  questionnaire-claim attribution, an inconsistent success definition in
  the stress-testing harness, missing narrator-backend provenance,
  overly-broad IAM grants, an incorrect ADR premise about JWT token
  types, missing `run_id` validation, several stale/self-fulfilling
  tests, and a hardcoded account ID. 13 of 15 were fixed and re-verified
  against real deployed AWS resources; the remaining 2 were a fully-atomic
  queue-write pattern and one item's live login-flow re-verification.

  **A second independent review then verified those fixes rather than
  re-scanning from scratch, and found that several of them introduced real
  regressions** — most seriously, three ordinary questions (an LDL trend,
  an HbA1c trend, an eGFR lookup) that were safe before round 1 started
  failing the safety check entirely, because of the exact fix meant to
  make grounding *stricter*. It also found one fix incomplete in a way
  that mattered (a cross-marker value/unit mixup the original safety fix
  was specifically supposed to close is still only partially closed), one
  new instance of a bug pattern round 1 had already fixed elsewhere but
  missed extending to two more code paths, and several documentation
  claims that overstated the post-fix state. All confirmed regressions and
  the newly-found instance were fixed and covered by new regression tests;
  two items (the cross-marker binding gap, and a processing-lease/
  reconciliation gap for duplicate SQS deliveries) were deliberately left
  open, the same way the queue-write pattern above was — they need a
  real design change, not a quick patch, and are documented honestly as
  open rather than silently fixed. **Both were later closed (2026-09-05)**:
  `GroundedFact` gained an optional `display_name`, and value+unit
  grounding now requires the correct marker's name to appear near a
  matched value+unit whenever one is set, closing the cross-marker swap
  without the full structured-claim rewrite the review suggested;
  `process_job.py` gained a `processing_lease_expires_at` field so a
  RUNNING record can only be re-claimed once the prior attempt's lease
  has actually expired (DynamoDB's own atomic compare-and-swap, no
  fencing token needed), and a new `reconcile_dlq.py` marks a DLQ'd run's
  record `FAILED` instead of leaving it stuck forever. Full findings and
  disposition for all review passes:
  [`INDEPENDENT_REVIEW_FINDINGS.md`](INDEPENDENT_REVIEW_FINDINGS.md);
  full reasoning: `DECISIONS.md`.

  **A third independent review, scoped to the frontend and public
  hosting neither earlier pass had touched, found that a live hotfix
  made the same day (a `question_text` numeric-grounding exemption) had
  reopened a genuine fabrication bypass** — reverted outright rather than
  patched further, given the asymmetry between a false positive (safe
  answer replaced by the template) and a false negative (a fabricated
  number reaching the user). It also found a within-topic overclaim in
  the personalization summary (the same bug pattern round 2 had already
  fixed elsewhere, not extended to this pair — fixing it introduced a new
  numeric-grounding regression, caught by the full suite before calling
  it done), four real frontend bugs (run history surviving sign-out, a
  polling race from `setInterval`'s overlapping callbacks, a stuck-pending
  state after a tolerated polling error, unrendered images as a data-
  exfiltration vector, and undetected access-token expiry), and one
  incomplete exception boundary in `get_run.py`. All 8 fixed and
  re-verified — most live, in a real browser against both the local dev
  server and the redeployed public URL. Redeploying all 6 stacks during
  this round's own verification also surfaced and fixed a self-inflicted
  CORS regression (see `DECISIONS.md`) from omitting the two-pass
  deployment's `CARE_AGENT_WORKBENCH_URL` env var. Full findings:
  [`INDEPENDENT_REVIEW_FINDINGS.md`](INDEPENDENT_REVIEW_FINDINGS.md).

  **Post-review cleanup, in priority order**: the two backlog items round
  2 had deliberately left open (cross-marker value/unit binding; an SQS
  processing lease + DLQ reconciliation), the frontend's first automated
  tests (zero before this, despite `auth.ts`/`AskForm.tsx` being the
  source of several of round 3's real bugs), Bedrock cost protection (an
  API-wide throttle plus an opt-in monthly Budget alert), and a
  capability-based regression eval (`care_agent.eval`) that turns
  `data/sample_questions.json`'s long-standing `expected_capabilities`
  labels from documentation nobody checked into an automated gate — which
  caught a real false positive in the cross-marker fix on its very first
  run (see `DECISIONS.md`, 2026-09-06). Full reasoning for all of the
  above: `DECISIONS.md`; eval pass-rate history over time:
  [`EVAL_HISTORY.md`](EVAL_HISTORY.md).

  **Beyond the review cleanup**, three further asks in priority order:
  a real CD pipeline (`CiCdStack` -- GitHub Actions deploys via OIDC, no
  stored AWS credential, gated by a required-reviewer approval on a
  `production` GitHub environment rather than a second AWS account); an
  automated `cdk-nag` security gate (`AwsSolutionsChecks`, wired into
  `cdk synth`/`cdk deploy` themselves so the existing CI `infra` job
  enforces it for free) with every finding either fixed (Cognito password
  symbols, DynamoDB point-in-time recovery, every Lambda bumped to Python
  3.13, Step Functions logging + X-Ray tracing, API Gateway access
  logging) or suppressed with a written, per-finding reason
  (`infra/nag_suppressions.py`); and a real, live bug the cdk-nag
  rollout's own post-deploy smoke test caught along the way -- a
  numbered list whose ordinal markers were wrapped in Markdown bold
  made `safety.py`'s ordinal-list exemption invisible, silently
  discarding otherwise-safe real Bedrock answers in favor of the mock
  fallback. All three deployed and live-verified; see `DECISIONS.md`
  (2026-09-06 entries) for the full account, including the version
  incompatibility (`cdk-nag` 3.0.2 vs this project's aws-cdk-lib/jsii
  combination) and two other live deploy mistakes caught and fixed
  before they could cause real harm.

## Cost notes

- Lambda / DynamoDB / API Gateway / Step Functions / Cognito are effectively
  free or very cheap at this scale.
- Bedrock is billed per token — the main real cost driver.
- OpenSearch Serverless has a nonzero minimum-capacity cost even idle; if
  Phase 5 uses it, tear it down when not actively experimenting. Aurora
  Serverless v2 + pgvector is the cheaper alternative for the same
  experiment.
