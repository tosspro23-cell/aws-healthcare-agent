# Deployment Strategy Draft

This is a draft, scoped to what this prototype would need to go from "runs
on my machine" to "safely serving real users behind an API," and how I'd
validate it stays safe once it's there. It assumes the surrounding product
(auth, user data storage, the actual bloodwork/questionnaire pipelines) is
owned by other services — this document covers the agent service itself.

## 1. How I'd deploy this safely

- **Ship the deterministic core first, LLM narration as a flagged
  enhancement.** The mock narrator has no external dependency and is fully
  covered by tests; it can go behind a real endpoint immediately. The LLM
  narrator (`CARE_AGENT_NARRATOR_BACKEND=anthropic`) should launch behind a
  feature flag / gradual rollout (see §3), not on day one, precisely because
  it's the one part of the pipeline whose output isn't deterministic.
- **Stateless service, per-request pipeline.** `HealthAgent.ask()` has no
  mutable state between calls (the data store, catalog, and retriever are
  read-only), so the service can scale horizontally behind a load balancer
  with no session affinity.
- **Fail closed, not open.** If the narrator (mock or LLM) produces text
  that fails a safety check, the agent already falls back to the
  deterministic narrator rather than returning unverified text (see
  `agent.py`). In production, if *even the fallback* fails safety checks
  (which would indicate a bug, not bad luck — the mock narrator is
  templated) the service should return a safe static refusal
  ("I can't answer that right now — please talk to a clinician") and page
  on-call, rather than ever emit unverified/unsafe text.
- **Least-privilege data access.** The service account running this agent
  should only be able to read the specific user's records for the duration
  of one request (e.g., a short-lived, request-scoped credential or a
  data-access-layer that enforces `user_id` scoping the way `DataStore`
  does here) — never a broad "read all users' health data" credential.

## 2. Configuration and environment requirements

| Setting | Purpose | Required? |
|---|---|---|
| `CARE_AGENT_NARRATOR_BACKEND` | `mock` (default), `anthropic`, or `ollama` | No — defaults to the safe deterministic path |
| `ANTHROPIC_API_KEY` | Only read if the `anthropic` backend is selected | No, unless cloud LLM narration is enabled |
| `OLLAMA_HOST` / `OLLAMA_MODEL` | Only read if the `ollama` backend is selected; a locally/privately hosted Ollama instance | No, unless local LLM narration is enabled |
| Data source config | In this prototype, local JSON/SQLite; in production, replace `DataStore`/`BiomarkerCatalog` with the real profile/bloodwork/questionnaire/catalog services behind the same interface | Yes |
| Retrieval corpus location | `knowledge_base.jsonl` today; in production, a versioned, reviewed content store | Yes |

No secrets are hardcoded, and the default configuration requires zero paid
API access, per this project's constraint — this also means the *default*
production configuration has the smallest possible blast radius (no
third-party data egress) unless a team explicitly opts into the LLM path.
If narration beyond templates is wanted but sending any grounded health text
to a third party is not acceptable (a real possibility for this kind of
data), self-hosting an Ollama-compatible model on infrastructure the team
controls (`ollama` backend) gets model-quality phrasing with the same
zero-external-egress property as the mock path — the tradeoff is
self-managed GPU/CPU capacity instead of a per-request API bill.

## 3. Rollout plan

1. **Shadow mode.** Run the agent against real (or de-identified) traffic
   alongside the existing system, log both outputs, do not show the new
   agent's answer to users. Compare grounding/safety-check pass rates and
   qualitative answer quality.
2. **Internal dogfooding.** Enable for employees/testers only, mock
   narrator only.
3. **Small-percentage canary** (e.g., 1–5% of eligible users), mock
   narrator only, with automatic rollback (§6) wired to the monitors in §4.
4. **Ramp to 100% on the mock narrator**, holding for a full weekly cycle to
   catch day-of-week usage pattern issues.
5. **LLM narrator canary**, same shape as steps 3–4, gated separately —
   its failure mode (an ungrounded or unsafe rephrasing) is different from
   the deterministic path's, so it earns its own canary rather than riding
   along with a mock-narrator rollout.
6. Each stage has an explicit go/no-go checklist tied to the metrics in §4,
   not a fixed timer.

## 4. Monitoring and observability

Every `AgentResponse.trace` already carries the data needed for
observability; the deployment work is piping it to metrics/logs, not adding
new instrumentation to the agent itself.

- **Safety-check pass rate**, broken out by check name
  (`no_diagnosis` / `no_dosing` / `numeric_grounding`) and by narrator
  backend. `numeric_grounding` failures on the *mock* narrator should be
  ~zero forever — a nonzero rate there means a real bug (a template
  producing a number reasoning.py didn't ground), not a data problem, and
  should page.
- **`narrator_fallback` rate** — how often the LLM backend (if enabled)
  gets overridden. A rising rate is an early warning the model or prompt
  drifted, independent of whether any single fallback caused user-visible
  harm.
- **Intent distribution** — tracks whether the classifier's coverage
  assumptions (tuned against 3 sample questions) hold against real question
  phrasing; a growing "general_bloodwork_question" share signals the
  classifier needs more rules or a model upgrade.
- **Retrieval health** — chunks-returned-per-query, top-score distribution;
  a drop signals a KB regression (bad reindex, corpus truncation) before
  users notice via answer quality.
- **Limitation-surface rate** — how often stale-data / missing-data /
  trend-unavailable limitations are shown; a spike could mean an upstream
  data-freshness regression (bloodwork sync broken) rather than an agent bug.
- **Latency** — p50/p95 per stage (retrieval, narration, safety checks)
  separately, since the LLM narration stage is the one with externally
  variable latency.
- **Standard service health**: error rate, request volume, dependency
  (data store / catalog / KB / model API) availability.

## 5. Privacy and logging considerations

- **Never log full health payloads at INFO level.** Log `trace` structure
  (intent, tool call names, chunk IDs, grounded-fact *counts* and
  *source_type*, safety-check pass/fail) rather than the biomarker values,
  questionnaire answers, or the free-text question itself, in default log
  streams. Full-payload logging, if needed for debugging, belongs in a
  short-retention, access-controlled debug store, opt-in per incident.
- **The `Sources:` line and trace already avoid a raw data dump** by design
  (`kb_answering_003` policy) — this should stay true for logs, not just
  user-facing text.
- **Declined questionnaire fields must never appear in logs either**, not
  just in the answer — a logging pipeline that captures the full
  `QuestionnaireContext` object needs the same field-level redaction the
  answer composer already respects.
- **Per-request data minimization**: the agent should only fetch the
  specific user's profile/bloodwork/questionnaire for the specific request,
  never bulk-load or cache other users' data in the same process.
- **Retention**: trace logs (safe to keep) vs. debug logs (full payload, if
  ever enabled) should have different retention policies, with the latter
  auto-expiring quickly.

## 6. Evaluation before production release

Beyond the unit/integration tests in this repo:

- **Golden-set regression eval**: freeze the three shipped sample questions
  (plus the edge cases in `tests/test_agent_edge_cases.py`) as a golden set
  with expected `expected_capabilities`-style assertions; run on every
  deploy, block release on regression.
- **Expanded question bank**: before wider rollout, build a larger set of
  paraphrases and adversarial inputs (ambiguous markers, multiple markers
  in one question, mixed-language input, more prompt-injection variants)
  and hold it to the same safety-check bar.
- **Clinical/content review**: have a qualified reviewer sign off on the
  `knowledge_base.jsonl` content and the narrator templates' phrasing
  before any real user sees them — this repo's guardrails check *grounding
  and forbidden patterns*, not medical accuracy of the underlying policy
  text itself.
- **Safety-check false-negative testing**: periodically red-team the
  `check_no_diagnosis` / `check_no_dosing` regex lists against new phrasing
  attempts; regexes are precise but not exhaustive, and this is the kind of
  gap that should be actively hunted, not just fixed reactively.

## 7. Rollback plan

- **Config-level rollback is instant**: unset `CARE_AGENT_NARRATOR_BACKEND` (or
  flip it back to `mock`) to drop the LLM path without a deploy, since the
  mock narrator is always available and always passes its own safety checks
  by construction.
- **Full service rollback**: standard blue/green or versioned-deploy
  rollback to the prior container image/version, since the service is
  stateless.
- **Triggers for automatic rollback**: `numeric_grounding` failure rate on
  the mock narrator > 0 (bug, not noise), any `no_diagnosis`/`no_dosing`
  failure that reaches a user (i.e., survives the fallback — should be
  impossible, so treat as sev-1), safety-check pass rate dropping below a
  set threshold, or error-rate/latency SLO breach.
- **Kill switch**: an independent, ops-controlled flag to disable the agent
  entirely and return a static "temporarily unavailable, please consult
  your clinician" response, separate from the narrator-backend flag, for
  cases where the issue is upstream (bad data feed) rather than in this
  service.

## 8. Comparing against current agent behavior

- **Offline**: run both the current and new agent over the same golden set
  and a sampled traffic replay; diff on safety-check pass rate, grounding
  completeness (are the same key values surfaced?), and limitation-handling
  correctness (does the new agent correctly refuse to invent a trend where
  the old one might have?).
- **Online (shadow mode, §3.1)**: log both agents' outputs for the same
  live requests without showing the new one to users; compare answer
  overlap on grounded facts, and track cases where the new agent surfaces a
  limitation the old one didn't (worth understanding whether that's a
  correctness improvement or a regression in helpfulness).
- **Human eval on a stratified sample**: reviewers rate both agents blind
  on grounding, safety, and helpfulness for the same question set,
  including the constructed edge cases (missing data, stale data, multiple
  markers, ambiguous questions) — these are exactly the cases most likely
  to differ between a template-grounded system and a more freeform one.
