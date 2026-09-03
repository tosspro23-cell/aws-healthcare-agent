# Architecture Reference

See the README's `## Architecture` section for the pipeline diagram and the
one-paragraph rationale per stage. This document is a lower-level reference:
the module map and an explicit policy → code traceability table.

## Module map

| Module | Responsibility | Depends on |
|---|---|---|
| `models.py` | Typed dataclasses shared everywhere; no logic | — |
| `data_store.py` | Loads/parses the JSON dataset, per-user scoped | `models` |
| `catalog.py` | Read-only SQLite biomarker metadata lookups | `models` |
| `retrieval.py` | BM25 + topic-tag ranking over `knowledge_base.jsonl` | `models` |
| `staleness.py` | Panel-age policy (fresh / potentially stale / stale) | — |
| `trend.py` | Two-point trend computation, unit/availability gated | `models` |
| `intent.py` | Regex-based question routing | — |
| `nlp.py` | Free-text → `concept_id` resolution | `catalog` |
| `reasoning.py` | Ranks markers, applies questionnaire modifiers, builds `Brief` | `models`, `catalog`, `staleness`, `trend` |
| `safety.py` | Post-hoc checks on final answer text | `models` |
| `narrator/mock_narrator.py` | Default deterministic text composer | `reasoning` |
| `narrator/_prompt.py` | Shared system prompt for every LLM-backed narrator | — |
| `narrator/llm_narrator.py` | Optional cloud (Anthropic) rephrasing layer | `narrator.mock_narrator`, `narrator._prompt`, `reasoning` |
| `narrator/openai_narrator.py` | Optional cloud (OpenAI) rephrasing layer | `narrator.mock_narrator`, `narrator._prompt`, `reasoning` |
| `narrator/google_narrator.py` | Optional cloud (Gemini) rephrasing layer | `narrator.mock_narrator`, `narrator._prompt`, `reasoning` |
| `narrator/ollama_narrator.py` | Optional local (Ollama) rephrasing layer, stdlib `urllib` only | `narrator.mock_narrator`, `narrator._prompt`, `reasoning` |
| `agent.py` | Orchestrates all of the above, builds the trace | everything |
| `cli.py` / `__main__.py` | Command-line entrypoint | `agent` |

Dependency direction is strictly one-way: `reasoning` never imports
`narrator`, and neither imports `agent`. This is what keeps "what fact was
used" (reasoning) separable from "how it was phrased" (narration) in tests,
not just in intent.

## Policy → code traceability

The sample `knowledge_base.jsonl` includes a set of `Nuaura mock policy`
entries that read like an explicit spec for this exact project. Where a
policy chunk maps directly to enforced code (not just narrative text), that
mapping is:

| KB policy id | Requirement | Enforced by |
|---|---|---|
| `kb_lipid_008` | Only describe a trend with ≥2 dated, same-unit measurements | `trend.compute_trend` |
| `kb_stale_data_002` | 6-month / 12-month staleness thresholds | `staleness.assess_staleness` |
| `kb_a1c_005` | Metabolic priority pattern (A1C + fasting glucose + triglycerides) | `reasoning.detect_metabolic_priority_pattern` |
| `kb_a1c_008` / `kb_grounding_004` | Don't diagnose from the dataset | `safety.check_no_diagnosis` |
| `kb_supplements_001` / `kb_supplements_003` | No supplement dose/timing | `safety.check_no_dosing` |
| `kb_grounding_002` | Every numeric claim must trace to retrieved/dataset context | `safety.verify_numeric_grounding` |
| `kb_grounding_003` | Don't recompute/relabel a classification the dataset supplies | `reasoning` reads `Biomarker.classification` as-is; `catalog` is metadata-only |
| `kb_questionnaire_002` | Never re-ask a declined field | No code path surfaces `QuestionnaireContext.declined`; regression-tested in `test_main_question_never_leaks_declined_field` |
| `kb_questionnaire_004` | Preferences shape the answer silently | `reasoning.build_questionnaire_modifiers` folds preferences into modifier text rather than listing them |
| `kb_nutrition_007` | Mention missing alcohol context only if material | `reasoning.alcohol_unknown_limitation` (gated on triglycerides being flagged) |
| `kb_safety_004` | Recommend clinician review when LDL + A1C both elevated | `agent.py`'s `clinician_review_recommended` computation |
| `kb_eval_001` | No wrong-user data leakage | `data_store._check_user` / `UnknownUserError` |
| `kb_answering_003` | No raw questionnaire/data dump | Narrator only emits selected, templated fields — never a JSON/field dump |

This table exists so a reviewer doesn't have to take "the agent follows the
policy corpus" on faith — each row names the test(s) or code path that
would fail if the behavior regressed.
