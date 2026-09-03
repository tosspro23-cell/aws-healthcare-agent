# Design Decision Log

Lightweight ADR-style log, one entry per non-obvious decision, added as the
AWS build-out progresses (see [`AWS_ROADMAP.md`](AWS_ROADMAP.md) for phase
status). Newest entries at the top. The point of keeping this is to have a
concrete artifact to compare against the equivalent decisions made on the
other cloud, not just a mental note of "why we did it this way."

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
