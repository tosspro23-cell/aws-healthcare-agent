# Design Decision Log

Lightweight ADR-style log, one entry per non-obvious decision, added as the
AWS build-out progresses (see [`AWS_ROADMAP.md`](AWS_ROADMAP.md) for phase
status). Newest entries at the top. The point of keeping this is to have a
concrete artifact to compare against the equivalent decisions made on the
other cloud, not just a mental note of "why we did it this way."

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
