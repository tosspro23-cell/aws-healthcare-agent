# infra/ — AWS CDK app

Deploys `../src/care_agent` AWS-native. See the top-level
[`../docs/AWS_ROADMAP.md`](../docs/AWS_ROADMAP.md) for phase status and
[`../docs/AWS_SETUP.md`](../docs/AWS_SETUP.md) for one-time AWS account
setup (IAM user + `aws configure`, done before anything here needs to touch
a real account).

## Layout

- `app.py` — CDK entrypoint; wires `AuthStack` + `DataStack` +
  `OrchestrationStack` + `QueueStack` into `ApiStack`. Builds the shared
  Lambda asset once and passes it to every stack that needs it.
- `stacks/auth_stack.py` — Cognito User Pool + Hosted UI domain + a public
  (no-secret) App Client configured for Authorization Code + PKCE.
- `stacks/data_stack.py` — DynamoDB run-state table + S3 evidence bucket.
- `stacks/orchestration_stack.py` — the Phase 3 async run path: a Step
  Functions state machine (`start → bounded retry → timeout → terminal
  state`, native `Retry`/`Catch`/`TimeoutSeconds`) plus its Task Lambdas
  (`mark_running`, `agent_task`, `record_result`) and the three API-facing
  Lambdas (`start_run`, `get_run`, `cancel_run`). Cancellation races the
  state machine's own finalization via a DynamoDB conditional write
  (`ConditionExpression: status = RUNNING`) — see the module docstring and
  `../../docs/DECISIONS.md` for the full reasoning.
- `stacks/queue_stack.py` — the stress-test follow-up: an SQS-buffered
  alternative async path, built specifically to compare against
  `orchestration_stack.py`'s retry-based approach (see the module
  docstring and `../../docs/STRESS_TEST.md`/`DECISIONS.md`). `POST /jobs`
  (`enqueue_job.py`) enqueues onto SQS and returns immediately; an
  SQS-triggered `process_job.py` consumes at a bounded
  `max_concurrency=5` regardless of queue depth, with a DLQ for anything
  that fails repeatedly. Polling reuses the existing `GET /runs/{run_id}`
  unchanged.
- `stacks/api_stack.py` — API Gateway HTTP API + Lambda integrations, with
  a Cognito JWT authorizer on every route (enforced at the gateway, before
  any Lambda ever runs — no auth-specific code in any handler). Routes:
  `POST /ask` (Phase 1, synchronous), `POST /runs` / `GET /runs/{run_id}` /
  `POST /runs/{run_id}/cancel` (Phase 3, async, Step Functions),
  `POST /jobs` (stress-test follow-up, async, SQS-buffered).
- `stacks/bedrock_grant.py` — Phase 4: shared `grant_bedrock_invoke(fn)`
  helper, used by both `AskHandler` and `AgentTaskHandler` (the only two
  Lambdas that call `HealthAgent.ask()`). Scopes `bedrock:InvokeModel` to
  exactly the 4 ARNs the deployed model needs (the cross-region inference
  profile + its 3 routed foundation-model ARNs) — see the module
  docstring and `../../docs/DECISIONS.md` for why a cross-region profile
  needs all 4, not just the profile ARN.
- `lambda_src/adapter.py` — the synchronous `/ask` handler: a thin
  translation layer between an API Gateway event and
  `care_agent.HealthAgent`. All actual reasoning/safety/retrieval logic
  stays in `care_agent`, unchanged.
- `lambda_src/agent_runtime.py` — the shared `HealthAgent` construction
  `adapter.py` and `agent_task.py` both use, so the two paths resolve the
  dataset location identically.
- `lambda_src/agent_task.py`, `mark_running.py`, `record_result.py`,
  `start_run.py`, `get_run.py`, `cancel_run.py` — the Phase 3 Lambdas (see
  `orchestration_stack.py` above for what each does).
- `lambda_src/enqueue_job.py`, `process_job.py` — the SQS-buffered path's
  two Lambdas (see `queue_stack.py` above).
- `build_lambda_asset.py` — assembles one shared Lambda deployment
  directory (every handler module + `care_agent` + `data/`) as a plain
  file copy (no Docker/pip bundling needed: `care_agent` has zero
  third-party runtime dependencies for its default mock-narrator path, and
  `boto3` already ships in the Lambda runtime image). Every `Function`
  construct across every stack points at a different `<module>.handler`
  within this same asset.
- `scripts/get_dev_token.py` — walks the Authorization Code + PKCE flow
  against the deployed Hosted UI (opens your browser, catches the redirect
  locally, exchanges the code for tokens) so you can get a real bearer
  token to test the protected routes with.
- `scripts/stress_test.py` — live (not CI, real cost) adversarial/load/
  persistence harness: concurrent bursts against the sync, Step-Functions-
  async, and SQS-buffered paths (`burst-sync` / `burst-async` /
  `burst-queue`), a curated real-Bedrock prompt-injection sweep
  (`adversarial`), and a repeated start-then-cancel race checking
  DynamoDB consistency each time (`race`). Bypasses API Gateway/Cognito by
  design. See `../docs/STRESS_TEST.md` for the methodology and results.
- `tests/test_stacks.py` / `tests/test_orchestration_stack.py` —
  assertions against the synthesized CloudFormation (not just "does
  `cdk synth` exit 0"): correct partition key, on-demand billing, public
  access blocked, every route requires JWT auth, the App Client is a
  public/no-secret client, the state machine's retry/timeout/catch/choice
  routing is actually configured as intended, no wildcard IAM resources.
- `tests/test_adapter.py` / `tests/test_orchestration_lambdas.py` — every
  Lambda handler's own logic, against `moto`-mocked DynamoDB/S3/Step
  Functions. No real AWS account, no network call. Includes the
  terminal-state race directly: `record_result` and `cancel_run` both
  attempting to finalize the same run_id, and asserting exactly one wins.
- `tests/test_get_dev_token.py` — the pure/testable parts of the token
  script (PKCE generation, URL/request construction). The interactive
  parts (browser + real login) aren't covered by an automated test, same
  as `docs/AWS_SETUP.md` being a manual walkthrough.
- `tests/test_live_endpoint_smoke.py` — end-to-end checks against a
  *deployed* endpoint: no-token/garbage-token → 401 always run if
  `CARE_AGENT_API_URL` is set; the authenticated behavioral checks
  additionally need `CARE_AGENT_ACCESS_TOKEN`. Never runs in CI.

## Local setup

```bash
cd infra
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
npm install -g aws-cdk   # if not already installed

ruff check . --line-length=140
mypy stacks app.py lambda_src build_lambda_asset.py scripts/get_dev_token.py scripts/stress_test.py --ignore-missing-imports
pytest tests/ -v
cdk synth   # validates the app compiles; no AWS credentials required
```

## Deploying (needs a configured AWS profile — see `../docs/AWS_SETUP.md`)

```bash
cdk bootstrap   # once per account+region
cdk deploy --all
```

Then get a token and point the live smoke test at the deployed API:

```bash
export CARE_AGENT_API_URL="$(aws cloudformation describe-stacks \
    --stack-name CareAgentApiStack \
    --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
    --output text)"

python scripts/get_dev_token.py   # opens your browser to sign up / sign in
eval "$(python scripts/get_dev_token.py | grep ^export)"   # sets CARE_AGENT_ACCESS_TOKEN

pytest tests/test_live_endpoint_smoke.py -v
```

## Useful CDK commands

- `cdk ls` — list all stacks in the app
- `cdk synth` — emit the synthesized CloudFormation template
- `cdk diff` — compare the deployed stack against current code
- `cdk deploy --all` — deploy all four stacks
- `cdk destroy --all` — tear everything down (all stacks use
  `RemovalPolicy.DESTROY` deliberately, since this is a demo/learning
  project, not a system holding real data worth protecting from deletion)
