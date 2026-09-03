# Design Decision Log

Lightweight ADR-style log, one entry per non-obvious decision, added as the
AWS build-out progresses (see [`AWS_ROADMAP.md`](AWS_ROADMAP.md) for phase
status). Newest entries at the top. The point of keeping this is to have a
concrete artifact to compare against the equivalent decisions made on the
other cloud, not just a mental note of "why we did it this way."

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
