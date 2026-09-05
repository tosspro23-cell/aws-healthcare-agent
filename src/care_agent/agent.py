"""Orchestrator: wires data access, retrieval, reasoning, narration, and
safety into a single ``HealthAgent.ask()`` call that returns an
``AgentResponse`` (answer text + a full execution trace).

Pipeline (also see ``docs/ARCHITECTURE.md``):

1. classify intent (rule-based)
2. load profile / bloodwork / questionnaire for the requested user only
3. resolve any biomarker names mentioned in the question text
4. rank flagged markers deterministically (severity x catalog importance)
5. retrieve supporting knowledge-base chunks (BM25 + topic-tag boost)
6. apply questionnaire-driven modifiers and safety cautions
7. narrate (mock by default; optional LLM backend, always re-verified)
8. run safety checks on the final text; fall back to the mock narration if
   an LLM backend's output fails any check
"""

from __future__ import annotations

import os
from pathlib import Path

from care_agent.catalog import DEFAULT_CATALOG_PATH, BiomarkerCatalog
from care_agent.data_store import DEFAULT_DATA_DIR, DataStore
from care_agent.intent import PRIORITY_FOCUS, RED_FLAG, SUPPLEMENT_SAFETY, TREND_CHECK, classify
from care_agent.models import (
    AgentResponse,
    AgentTrace,
    GroundedFact,
    Limitation,
    ToolCall,
)
from care_agent.narrator.mock_narrator import MockNarrator
from care_agent.nlp import find_concept_mentions
from care_agent.reasoning import (
    CONCEPT_TOPIC_TAGS,
    INTENT_TOPIC_TAGS,
    Brief,
    alcohol_unknown_limitation,
    build_questionnaire_modifiers,
    build_supplement_cautions,
    detect_metabolic_priority_pattern,
    rank_focus_markers,
    staleness_limitation,
)
from care_agent.retrieval import DEFAULT_KB_PATH, KnowledgeRetriever
from care_agent.safety import run_safety_checks
from care_agent.trend import compute_trend


def _select_narrator():
    backend = os.environ.get("CARE_AGENT_NARRATOR_BACKEND", "mock").lower()
    if backend == "mock":
        return MockNarrator()
    if backend == "anthropic":
        from care_agent.narrator.llm_narrator import AnthropicNarrator

        return AnthropicNarrator()
    if backend == "ollama":
        from care_agent.narrator.ollama_narrator import OllamaNarrator

        return OllamaNarrator()
    if backend == "openai":
        from care_agent.narrator.openai_narrator import OpenAINarrator

        return OpenAINarrator()
    if backend == "google":
        from care_agent.narrator.google_narrator import GoogleNarrator

        return GoogleNarrator()
    if backend == "bedrock":
        from care_agent.narrator.bedrock_narrator import BedrockNarrator

        return BedrockNarrator()
    raise ValueError(
        f"Unknown CARE_AGENT_NARRATOR_BACKEND={backend!r}; expected 'mock', 'anthropic', 'openai', 'google', 'bedrock', or 'ollama'."
    )


class HealthAgent:
    def __init__(
        self,
        data_dir: Path | str = DEFAULT_DATA_DIR,
        catalog_path: Path | str = DEFAULT_CATALOG_PATH,
        kb_path: Path | str = DEFAULT_KB_PATH,
        narrator=None,
    ):
        self.data_store = DataStore(data_dir)
        self.catalog = BiomarkerCatalog(catalog_path)
        self.retriever = KnowledgeRetriever(kb_path=kb_path)
        self.narrator = narrator or _select_narrator()
        self._mock_narrator = MockNarrator()

    def ask(self, user_id: str, question_text: str, question_id: str | None = None) -> AgentResponse:
        trace = AgentTrace(question_id=question_id, user_id=user_id, intent="", narrator_backend=self.narrator.backend_name)

        intent_result = classify(question_text)
        trace.intent = intent_result.intent
        trace.tool_calls.append(
            ToolCall(name="classify_intent", args={"question_text": question_text}, result_summary=intent_result.intent)
        )

        profile = self.data_store.get_user_profile(user_id)
        trace.tool_calls.append(
            ToolCall(name="get_user_profile", args={"user_id": user_id}, result_summary=f"display_name={profile.display_name!r}")
        )

        bloodwork = self.data_store.get_bloodwork(user_id)
        trace.tool_calls.append(
            ToolCall(
                name="get_bloodwork",
                args={"user_id": user_id},
                result_summary=f"latest_panel={'present' if bloodwork.latest_panel else 'missing'}, "
                f"previous_panels={len(bloodwork.previous_panels)}",
            )
        )

        questionnaire = self.data_store.get_questionnaire_context(user_id)
        trace.tool_calls.append(
            ToolCall(
                name="get_questionnaire_context",
                args={"user_id": user_id},
                result_summary=f"facts={len(questionnaire.facts)}, cautions={len(questionnaire.cautions)}",
            )
        )

        mentioned_concepts = find_concept_mentions(question_text, self.catalog)
        trace.tool_calls.append(
            ToolCall(name="find_concept_mentions", args={"question_text": question_text}, result_summary=str(mentioned_concepts))
        )

        brief = Brief(intent=intent_result.intent, mentioned_concepts=mentioned_concepts)
        brief.red_flag = intent_result.intent == RED_FLAG

        allowed_dates: set[str] = set()
        for panel in bloodwork.all_panels_newest_first():
            allowed_dates.add(panel.measurement_date)

        if not brief.red_flag:
            stale_limitation, staleness_result = staleness_limitation(bloodwork.latest_panel)
            brief.staleness = staleness_result
            if stale_limitation:
                brief.limitations.append(stale_limitation)
                if stale_limitation.kind == "stale_data" and staleness_result is not None:
                    brief.grounded_facts.append(
                        GroundedFact(
                            claim="panel age in days",
                            source_type="bloodwork",
                            source_ref=bloodwork.latest_panel.panel_id if bloodwork.latest_panel else "none",
                            numeric_values=(float(staleness_result.age_days),),
                        )
                    )

            latest_panel = bloodwork.latest_panel
            if latest_panel is not None:
                trace.tool_calls.append(
                    ToolCall(
                        name="rank_focus_markers",
                        args={"panel_id": latest_panel.panel_id},
                        result_summary=f"{len(latest_panel.biomarkers)} biomarkers in panel",
                    )
                )
                brief.focus_items = rank_focus_markers(latest_panel, self.catalog, set(mentioned_concepts))
                brief.metabolic_pattern_detected = detect_metabolic_priority_pattern(brief.focus_items)

                for item in brief.focus_items:
                    brief.grounded_facts.append(
                        GroundedFact(
                            claim=f"{item.marker.display_name} = {item.marker.value} {item.marker.unit} "
                            f"({item.marker.classification}) on {latest_panel.measurement_date}",
                            source_type="bloodwork",
                            source_ref=f"{latest_panel.panel_id}:{item.marker.concept_id}",
                            numeric_values=(float(item.marker.value),),
                            unit=item.marker.unit,
                        )
                    )

                ldl = latest_panel.get("ldl_c_mg_dl")
                a1c = latest_panel.get("hba1c_percent")
                flagged_ids = {it.marker.concept_id for it in brief.focus_items}
                brief.clinician_review_recommended = (
                    ("ldl_c_mg_dl" in flagged_ids and "hba1c_percent" in flagged_ids)
                    or "review_with_clinician" in latest_panel.overall_flags
                ) and bool(ldl and a1c)

                for concept_id in mentioned_concepts:
                    marker = latest_panel.get(concept_id)
                    if marker is not None:
                        brief.mentioned_markers[concept_id] = marker
                        if not any(gf.source_ref.endswith(concept_id) for gf in brief.grounded_facts):
                            brief.grounded_facts.append(
                                GroundedFact(
                                    claim=f"{marker.display_name} = {marker.value} {marker.unit} on {latest_panel.measurement_date}",
                                    source_type="bloodwork",
                                    source_ref=f"{latest_panel.panel_id}:{concept_id}",
                                    numeric_values=(float(marker.value),),
                                    unit=marker.unit,
                                )
                            )

            # -- questionnaire modifiers -----------------------------------
            modifiers = build_questionnaire_modifiers(questionnaire)
            brief.questionnaire_modifiers = modifiers
            for mod in modifiers:
                brief.grounded_facts.append(mod.grounded_fact)

            triglycerides_relevant = (
                intent_result.intent == PRIORITY_FOCUS
                or "triglycerides_mg_dl" in mentioned_concepts
                or (intent_result.intent == TREND_CHECK and mentioned_concepts and mentioned_concepts[0] == "triglycerides_mg_dl")
            )
            if triglycerides_relevant:
                alcohol_limitation = alcohol_unknown_limitation(questionnaire, brief.focus_items)
                if alcohol_limitation:
                    brief.limitations.append(alcohol_limitation)

            if intent_result.intent == SUPPLEMENT_SAFETY:
                cautions = build_supplement_cautions(questionnaire, profile)
                brief.supplement_cautions = cautions
                for c in cautions:
                    brief.grounded_facts.append(c.grounded_fact)

            if intent_result.intent == TREND_CHECK and mentioned_concepts:
                concept_id = mentioned_concepts[0]
                trace.tool_calls.append(ToolCall(name="compute_trend", args={"concept_id": concept_id}, result_summary=""))
                trend = compute_trend(bloodwork, concept_id)
                brief.trend_result = trend
                brief.previous_panel_dates = [p.measurement_date for p in bloodwork.previous_panels]
                trace.tool_calls[-1] = ToolCall(
                    name="compute_trend",
                    args={"concept_id": concept_id},
                    result_summary=f"available={trend.available}, direction={trend.direction}",
                )
                if trend.latest_value is not None:
                    brief.grounded_facts.append(
                        GroundedFact(
                            claim=f"{concept_id} latest value",
                            source_type="bloodwork",
                            source_ref=f"trend:{concept_id}:latest",
                            numeric_values=(float(trend.latest_value),),
                        )
                    )
                if trend.previous_value is not None:
                    brief.grounded_facts.append(
                        GroundedFact(
                            claim=f"{concept_id} previous value",
                            source_type="bloodwork",
                            source_ref=f"trend:{concept_id}:previous",
                            numeric_values=(float(trend.previous_value),),
                        )
                    )
                if not trend.available and not mentioned_concepts:
                    pass
            elif intent_result.intent == TREND_CHECK and not mentioned_concepts:
                brief.limitations.append(
                    Limitation(
                        kind="ambiguous_question",
                        detail="Could not identify which biomarker the trend question refers to.",
                    )
                )

            # -- retrieval ---------------------------------------------------
            topic_tags: set[str] = set(INTENT_TOPIC_TAGS.get(intent_result.intent, set()))
            for item in brief.focus_items[:5]:
                topic_tags |= CONCEPT_TOPIC_TAGS.get(item.marker.concept_id, set())
            for concept_id in mentioned_concepts:
                topic_tags |= CONCEPT_TOPIC_TAGS.get(concept_id, set())
            for mod in brief.questionnaire_modifiers:
                topic_tags.add(mod.topic)

            retrieved = self.retriever.retrieve(question_text, top_k=6, topic_filter=topic_tags)
            brief.retrieved_chunks = retrieved
            trace.retrieved_chunks = retrieved
            trace.tool_calls.append(
                ToolCall(
                    name="retrieve_knowledge",
                    args={"query": question_text, "topic_filter": sorted(topic_tags)},
                    result_summary=f"{len(retrieved)} chunks: {[rc.chunk.id for rc in retrieved]}",
                )
            )

        trace.grounded_facts = brief.grounded_facts
        trace.limitations = brief.limitations

        # -- narrate + verify --------------------------------------------------
        answer_text = self.narrator.compose(brief, question_text, profile)
        report = run_safety_checks(answer_text, brief.grounded_facts, allowed_dates)

        used_fallback = False
        if not report.passed and self.narrator.backend_name != "mock":
            # An LLM (or any non-mock) narrator failed a safety/grounding check.
            # Fall back to the deterministic narrator rather than return
            # unverified text.
            answer_text = self._mock_narrator.compose(brief, question_text, profile)
            report = run_safety_checks(answer_text, brief.grounded_facts, allowed_dates)
            used_fallback = True

        trace.safety_checks = list(report.checks)
        if used_fallback:
            trace.safety_checks.append(
                type(report.checks[0])(
                    name="narrator_fallback",
                    passed=True,
                    detail=f"Fell back to mock narrator because backend {self.narrator.backend_name!r} failed a safety check.",
                )
            )

        return AgentResponse(answer=answer_text, trace=trace, safe=report.passed)
