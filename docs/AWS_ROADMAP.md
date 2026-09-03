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

- ⬜ Cognito User Pool + App Client.
- ⬜ API Gateway Cognito Authorizer on the protected routes.
- ⬜ Test/CLI client updated to do the auth-code + PKCE flow to get a token.
- Deliberately its own phase, separate from Phase 1, so auth configuration
  issues don't block getting the skeleton running end to end first.

## Phase 3 — Step Functions orchestration (reliability semantics)

- ⬜ Step Functions state machine implementing: start → bounded retry →
  timeout → cancellation → terminal state.
- ⬜ Retry/timeout via Step Functions' native Retry/Catch blocks.
- ⬜ Terminal-state ownership race handled via DynamoDB conditional writes
  (`ConditionExpression`) — the AWS analog of optimistic concurrency control
  (ETag/CAS-style patterns elsewhere).
- ⬜ Write up the comparison note in `DECISIONS.md`: same reliability
  requirement, how does the AWS-native implementation differ in shape from
  a Durable-Functions-style approach elsewhere?

## Phase 4 — Bedrock integration (highest priority)

- ⬜ Request model access in the Bedrock console (Anthropic models need
  separate enablement).
- ⬜ IAM policy scoped precisely to `bedrock:InvokeModel` on the specific
  model ARN — no broad Bedrock permissions.
- ⬜ `src/care_agent/narrator/bedrock_narrator.py` — same pluggable
  `Narrator` interface as `openai_narrator.py` / `ollama_narrator.py`,
  calling `bedrock-runtime`'s `Converse` API.
- ⬜ **Reuse the existing safety pipeline as-is** (`numeric_grounding`,
  `no_diagnosis`, `no_dosing`) — do not rewrite it. The test that matters:
  does the same safety net hold up against a materially different model
  output style?
- ⬜ Mocked unit tests following the `test_ollama_narrator.py` /
  `test_openai_narrator.py` pattern (HTTP/SDK layer mocked, CI never calls
  a real endpoint).
- ⬜ **At least one real, non-mocked Bedrock call**, with the real output and
  trace recorded as evidence (not just described) — this is the one piece
  that's a genuine capability gap to close, not just more of the same
  pattern already proven with Anthropic/OpenAI/Google/Ollama.

## Phase 5 — Vector retrieval experiment (optional, lowest priority)

- ⬜ Try vector retrieval over the existing 68-chunk `knowledge_base.jsonl`
  using either pgvector (Aurora Serverless) or OpenSearch Serverless.
- Not because keyword/BM25 retrieval is insufficient at this corpus size —
  it isn't — but as a hands-on learning exercise.
- ⬜ Tear down whatever gets provisioned afterward to avoid ongoing cost.

## Process checklist (apply at every phase, not just once)

- [ ] Adversarial/boundary tests added for the new surface (extreme values,
  empty input, injection attempts, all-normal-data cases) — not only a
  happy-path smoke test.
- [ ] `DECISIONS.md` updated with what was chosen and why.
- [ ] Self-review pass: does any comment or doc describe a safety check as
  stronger than it actually is? (E.g. "prevents hallucination" when it only
  proves a number has *some* source, not that it's the *right* source; or a
  policy written for one marker class getting applied to all markers by
  accident.) Check this explicitly before calling a phase "done."
- [ ] Get a second, independent AI session (not the one that wrote the code)
  to critically review the phase before moving on.

## Cost notes

- Lambda / DynamoDB / API Gateway / Step Functions / Cognito are effectively
  free or very cheap at this scale.
- Bedrock is billed per token — the main real cost driver.
- OpenSearch Serverless has a nonzero minimum-capacity cost even idle; if
  Phase 5 uses it, tear it down when not actively experimenting. Aurora
  Serverless v2 + pgvector is the cheaper alternative for the same
  experiment.
