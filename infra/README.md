# infra/ — AWS CDK app

Deploys `../src/care_agent` AWS-native. See the top-level
[`../docs/AWS_ROADMAP.md`](../docs/AWS_ROADMAP.md) for phase status and
[`../docs/AWS_SETUP.md`](../docs/AWS_SETUP.md) for one-time AWS account
setup (IAM user + `aws configure`, done before anything here needs to touch
a real account).

## Layout

- `app.py` — CDK entrypoint; wires `AuthStack` + `DataStack` into `ApiStack`.
- `stacks/auth_stack.py` — Cognito User Pool + Hosted UI domain + a public
  (no-secret) App Client configured for Authorization Code + PKCE.
- `stacks/data_stack.py` — DynamoDB run-state table + S3 evidence bucket.
- `stacks/api_stack.py` — API Gateway HTTP API + Lambda integration, with a
  Cognito JWT authorizer on `/ask` (enforced at the gateway, before the
  Lambda ever runs — no auth-specific code in the handler itself).
- `lambda_src/adapter.py` — the Lambda handler itself: a thin translation
  layer between an API Gateway event and `care_agent.HealthAgent`. All
  actual reasoning/safety/retrieval logic stays in `care_agent`, unchanged.
- `build_lambda_asset.py` — assembles the Lambda deployment package as a
  plain file copy (no Docker/pip bundling needed: `care_agent` has zero
  third-party runtime dependencies for its default mock-narrator path, and
  `boto3` already ships in the Lambda runtime image).
- `scripts/get_dev_token.py` — walks the Authorization Code + PKCE flow
  against the deployed Hosted UI (opens your browser, catches the redirect
  locally, exchanges the code for tokens) so you can get a real bearer
  token to test the now-protected `/ask` route with.
- `tests/test_stacks.py` — assertions against the synthesized CloudFormation
  (not just "does `cdk synth` exit 0"): correct partition key, on-demand
  billing, public access blocked, `/ask` requires JWT auth (not anonymous),
  the App Client is a public/no-secret client, no wildcard IAM resources.
- `tests/test_adapter.py` — the Lambda handler's own logic, against a
  `moto`-mocked DynamoDB table and S3 bucket. No real AWS account, no
  network call. (Auth doesn't need coverage here — API Gateway rejects
  unauthorized requests before the Lambda is invoked at all.)
- `tests/test_get_dev_token.py` — the pure/testable parts of the token
  script (PKCE generation, URL/request construction). The interactive
  parts (browser + real login) aren't covered by an automated test, same
  as `docs/AWS_SETUP.md` being a manual walkthrough.
- `tests/test_live_endpoint_smoke.py` — end-to-end checks against a
  *deployed* endpoint: no-token/garbage-token → 401 always run if
  `CARE_AGENT_API_URL` is set; the authenticated behavioral checks
  additionally need `CARE_AGENT_ID_TOKEN`. Never runs in CI.

## Local setup

```bash
cd infra
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
npm install -g aws-cdk   # if not already installed

ruff check . --line-length=140
mypy stacks app.py lambda_src build_lambda_asset.py scripts/get_dev_token.py --ignore-missing-imports
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
eval "$(python scripts/get_dev_token.py | grep ^export)"   # sets CARE_AGENT_ID_TOKEN

pytest tests/test_live_endpoint_smoke.py -v
```

## Useful CDK commands

- `cdk ls` — list all stacks in the app
- `cdk synth` — emit the synthesized CloudFormation template
- `cdk diff` — compare the deployed stack against current code
- `cdk deploy --all` — deploy all three stacks
- `cdk destroy --all` — tear everything down (all stacks use
  `RemovalPolicy.DESTROY` deliberately, since this is a demo/learning
  project, not a system holding real data worth protecting from deletion)
