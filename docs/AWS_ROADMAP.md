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
  3 calls, confirmed by re-querying before and after — independent proof
  the calls came from AWS-side infrastructure, not a local process. Full
  evidence: [`PHASE4_BEDROCK_EVIDENCE.md`](PHASE4_BEDROCK_EVIDENCE.md).

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
  in the common case and operationally simpler; SQS guarantees eventual
  success at any scale but makes callers wait longer during a real burst.
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

## Phase 6 — Frontend / Workbench (future milestone, not started)

Everything through Phase 5 is backend-only: real auth exists (Cognito
Hosted UI is a genuine login page), but nothing calls it except this
project's own terminal tooling (`curl`, `pytest`, `get_dev_token.py`). A
user-facing product needs an actual client — web or mobile — that:

- ⬜ Runs the Authorization Code + PKCE flow through an in-browser redirect
  (not a local Python script standing in for one).
- ⬜ Calls `/ask` (and, after Phase 3, `/runs` + `/runs/{run_id}` +
  `/runs/{run_id}/cancel`) with the resulting token.
- ⬜ Renders the answer, and ideally the grounding trace, in a UI a
  non-technical person could actually use.

Not started, not blocking anything else in this roadmap — flagged here
because the Azure counterpart already has a React/Vite "Workbench" doing
exactly this, and a frontend is the natural next comparison point once the
AWS-side backend phases are further along: same underlying API, is the
client-side auth/UX story simpler or harder to build against API Gateway +
Cognito than against Azure Functions + Entra/MSAL?

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
  tests, and a hardcoded account ID. **13 of 15 fixed and re-verified**
  (against real deployed AWS resources for anything about deployed
  behavior, not just moto); the remaining 2 are a fully-atomic
  queue-write pattern (needs a real outbox design, not a quick fix) and
  one item's live login-flow re-verification (needs a human at a real
  browser). Full findings and disposition:
  [`INDEPENDENT_REVIEW_FINDINGS.md`](INDEPENDENT_REVIEW_FINDINGS.md); full
  reasoning: `DECISIONS.md`.

## Cost notes

- Lambda / DynamoDB / API Gateway / Step Functions / Cognito are effectively
  free or very cheap at this scale.
- Bedrock is billed per token — the main real cost driver.
- OpenSearch Serverless has a nonzero minimum-capacity cost even idle; if
  Phase 5 uses it, tear it down when not actively experimenting. Aurora
  Serverless v2 + pgvector is the cheaper alternative for the same
  experiment.
