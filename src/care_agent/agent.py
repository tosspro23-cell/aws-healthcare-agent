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
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from care_agent.catalog import DEFAULT_CATALOG_PATH, BiomarkerCatalog
from care_agent.data_store import DEFAULT_DATA_DIR, DataStore
from care_agent.intent import COMPOUND_REASONING, PRIORITY_FOCUS, RED_FLAG, SUPPLEMENT_SAFETY, TREND_CHECK, classify
from care_agent.models import (
    AgentResponse,
    AgentTrace,
    Bloodwork,
    GroundedFact,
    Limitation,
    ToolCall,
)
from care_agent.narrator.mock_narrator import MockNarrator
from care_agent.nlp import find_concept_mentions
from care_agent.orchestrator import ToolExecutionContext, ToolPlanner, run_compound_reasoning
from care_agent.plausibility import assess_plausibility
from care_agent.reasoning import (
    CONCEPT_TOPIC_TAGS,
    INTENT_TOPIC_TAGS,
    Brief,
    alcohol_unknown_limitation,
    build_questionnaire_modifiers,
    build_supplement_cautions,
    detect_metabolic_priority_pattern,
    implausible_value_limitations,
    rank_focus_markers,
    staleness_limitation,
)
from care_agent.retrieval import DEFAULT_KB_PATH, KnowledgeRetriever
from care_agent.safety import run_safety_checks
from care_agent.trend import compute_trend

# Matches `AgentTrace.disposition`'s own type exactly (models.py) -- kept
# as one alias, not duplicated, so the two can't silently drift apart.
_Disposition = Literal["answered", "answered_after_soft_fallback", "answered_after_hard_fallback"]


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


def _select_retriever(kb_path):
    backend = os.environ.get("CARE_AGENT_RETRIEVER_BACKEND", "bm25").lower()
    if backend == "bm25":
        return KnowledgeRetriever(kb_path=kb_path)
    if backend == "chroma":
        from care_agent.retrieval.chroma_retriever import ChromaRetriever

        return ChromaRetriever(kb_path=kb_path)
    raise ValueError(f"Unknown CARE_AGENT_RETRIEVER_BACKEND={backend!r}; expected 'bm25' or 'chroma'.")


class HealthAgent:
    def __init__(
        self,
        data_dir: Path | str = DEFAULT_DATA_DIR,
        catalog_path: Path | str = DEFAULT_CATALOG_PATH,
        kb_path: Path | str = DEFAULT_KB_PATH,
        narrator=None,
        retriever=None,
    ):
        self.data_store = DataStore(data_dir)
        self.catalog = BiomarkerCatalog(catalog_path)
        self.retriever = retriever or _select_retriever(kb_path)
        self.narrator = narrator or _select_narrator()
        self._mock_narrator = MockNarrator()

    def _apply_source_data_checks(self, brief: Brief, trace: AgentTrace, bloodwork: Bloodwork) -> None:
        """Source-data staleness + plausibility checks -- shared by `ask()`
        (V1) and `ask_compound()` (V2) so both pipelines flag the same
        likely data-entry errors and out-of-date panels, independent of
        which tools or intent classifier built the rest of the `Brief`.

        Extracted from `ask()`'s own inline logic: an independent review
        found `ask_compound()` never called either check at all -- a V2
        answer built entirely from `get_marker_snapshot` would happily
        report a value flagged implausible (e.g. an LDL-C of 5000 mg/dL)
        or years out of date with no limitation attached, while the exact
        same question through `ask()` correctly surfaced both. This
        method is now the single place either check runs, so there is no
        way to add a third pipeline that forgets it the way V2 did. See
        docs/DECISIONS.md, 2026-09-21 entry.
        """
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
        if latest_panel is None:
            return

        # Independent of `Biomarker.classification`, so a value the
        # dataset happens to classify as "normal" still gets checked.
        # Only *flags* (via `brief.limitations`), never excludes a
        # marker from a caller's own ranking/pattern-detection -- see
        # `implausible_value_limitations`'s own docstring.
        implausible_limitations = implausible_value_limitations(latest_panel)
        if not implausible_limitations:
            return

        trace.tool_calls.append(
            ToolCall(
                name="implausible_value_limitations",
                args={"panel_id": latest_panel.panel_id},
                result_summary=f"{len(implausible_limitations)} marker(s) outside plausibility bounds",
            )
        )
        brief.limitations.extend(implausible_limitations)
        # Named `panel_marker`, not `marker` -- `ask()` (below) also binds
        # `marker` to a different, `Biomarker | None`-typed value in its
        # own scope; a shared name here previously made mypy infer this
        # loop's non-Optional narrowing across the whole method, rejecting
        # that later reassignment. See docs/DECISIONS.md, 2026-09-20 entry.
        for panel_marker in latest_panel.biomarkers:
            result = assess_plausibility(panel_marker)
            if result.is_plausible or result.bounds is None:
                continue
            # Grounds the flagged value itself -- without this, the
            # Limitation text above (which quotes `marker.value`) would
            # fail its own numeric grounding check purely because this
            # check introduced a number nothing else grounds yet.
            brief.grounded_facts.append(
                GroundedFact(
                    claim=(
                        f"{panel_marker.display_name} = {panel_marker.value} {panel_marker.unit} "
                        f"(flagged implausible) on {latest_panel.measurement_date}"
                    ),
                    source_type="bloodwork",
                    source_ref=f"{latest_panel.panel_id}:{panel_marker.concept_id}",
                    numeric_values=(float(panel_marker.value),),
                    unit=panel_marker.unit,
                    display_name=panel_marker.display_name,
                    display_name_aliases=self.catalog.aliases_for(panel_marker.concept_id),
                )
            )

    def ask(
        self,
        user_id: str,
        question_text: str,
        question_id: str | None = None,
        persona: str = "patient",
        prior_grounded_facts: list[GroundedFact] | None = None,
        current_question: str | None = None,
    ) -> AgentResponse:
        """`current_question`, if given, is the bare live question with no
        prior-turn Q&A folded in -- used *only* for `classify()` below,
        never for narration (which keeps reading `question_text`, since a
        non-mock narrator benefits from seeing conversational context on
        purpose). Defaults to `question_text` when omitted, reproducing
        today's behavior for any caller that doesn't send it.

        An independent review found that `Workbench.tsx`'s client-side
        conversation-memory feature (folding prior Q&A into `question_text`
        for continuity) could hijack this keyword classifier's routing:
        "Is my LDL getting worse?" alone classifies as `trend_check`, but
        wrapped with a prior turn's "What should I focus on first"
        answer, keywords from that *old* answer routed the *new* question
        to `priority_focus` instead. `classify()` has no notion of a
        "New question:" boundary inside a blob of text -- the fix is to
        never hand it one, not to teach it to find one. See
        docs/DECISIONS.md.
        """
        start_time = time.monotonic()
        trace = AgentTrace(
            question_id=question_id,
            user_id=user_id,
            intent="",
            narrator_backend=self.narrator.backend_name,
            retriever_backend=self.retriever.backend_name,
        )

        effective_current = current_question or question_text
        intent_result = classify(effective_current)
        trace.intent = intent_result.intent
        trace.tool_calls.append(
            ToolCall(name="classify_intent", args={"question_text": effective_current}, result_summary=intent_result.intent)
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
        brief.persona = persona

        allowed_dates: set[str] = set()
        for panel in bloodwork.all_panels_newest_first():
            allowed_dates.add(panel.measurement_date)

        if not brief.red_flag:
            self._apply_source_data_checks(brief, trace, bloodwork)

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
                            display_name=item.marker.display_name,
                            display_name_aliases=self.catalog.aliases_for(item.marker.concept_id),
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
                                    display_name=marker.display_name,
                                    display_name_aliases=self.catalog.aliases_for(concept_id),
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
                # `brief.mentioned_markers` was already populated above (from
                # the same `mentioned_concepts` this trend's concept_id comes
                # from) whenever the marker exists in the latest panel --
                # None otherwise (e.g. a marker only present in an older
                # panel), which correctly leaves these trend facts without a
                # name to check against rather than fabricating one from the
                # raw concept_id string, which would never appear verbatim
                # in any real prose and would just make every trend answer
                # fail the safety check.
                trend_display_name = brief.mentioned_markers[concept_id].display_name if concept_id in brief.mentioned_markers else None
                trend_display_aliases = self.catalog.aliases_for(concept_id) if trend_display_name else ()
                if trend.latest_value is not None:
                    brief.grounded_facts.append(
                        GroundedFact(
                            claim=f"{concept_id} latest value",
                            source_type="bloodwork",
                            source_ref=f"trend:{concept_id}:latest",
                            numeric_values=(float(trend.latest_value),),
                            unit=trend.unit,
                            display_name=trend_display_name,
                            display_name_aliases=trend_display_aliases,
                        )
                    )
                if trend.previous_value is not None:
                    brief.grounded_facts.append(
                        GroundedFact(
                            claim=f"{concept_id} previous value",
                            source_type="bloodwork",
                            source_ref=f"trend:{concept_id}:previous",
                            numeric_values=(float(trend.previous_value),),
                            unit=trend.unit,
                            display_name=trend_display_name,
                            display_name_aliases=trend_display_aliases,
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

        return self._narrate_and_verify(brief, question_text, profile, trace, allowed_dates, start_time, prior_grounded_facts)

    def _narrate_and_verify(
        self,
        brief: Brief,
        question_text: str,
        profile,
        trace: AgentTrace,
        allowed_dates: set[str],
        start_time: float,
        prior_grounded_facts: list[GroundedFact] | None = None,
    ) -> AgentResponse:
        """Narrate a fully-built `Brief`, verify it, and fall back to the
        mock narrator on any check failure -- the one safety gate every
        answer this agent produces goes through, regardless of which
        pipeline built the `Brief`.

        Extracted unchanged from `ask()`'s own tail (see `docs/DECISIONS.md`,
        2026-09-20 entry) so `ask_compound()` -- a second, additive way to
        *build* a `Brief` via tool-calling instead of the fixed intent
        classifier -- reuses the exact same narrate+verify+fallback code,
        not a reimplementation of it. `ask()`'s own behavior is unchanged
        by this extraction: verified by the full existing test suite
        passing identically before and after.

        `prior_grounded_facts` -- already independently re-verified by the
        caller (the Lambda handler re-fetches them by `run_id`, checking
        ownership, never trusting a client's own claim) -- widens *only*
        the grounding check below, never `trace.grounded_facts` itself:
        the Evidence panel must keep showing only what *this* turn's own
        tool calls actually gathered, not an older turn's facts repeated
        as if they were newly computed. This is what lets a genuinely
        already-verified number from earlier in the same conversation
        survive this turn's `numeric_grounding` check instead of being
        treated as an invented one -- see docs/DECISIONS.md.

        `start_time` is this call's entry point's own `time.monotonic()`
        timestamp -- this is the single tail-call every return path of
        `ask()`/`ask_compound()` goes through, so it's the one place that
        can set `trace.total_duration_ms` for all of them.
        """
        facts_for_grounding = brief.grounded_facts + list(prior_grounded_facts or ())
        # Record exactly what cross-turn evidence widened this turn's own
        # grounding check -- kept separate from `trace.grounded_facts`
        # (this turn's own tools only) so the trace stays self-sufficient
        # to replay the safety decision later. See
        # `AgentTrace.prior_evidence`'s own docstring.
        trace.prior_evidence = list(prior_grounded_facts or ())

        _compose_start = time.monotonic()
        answer_text = self.narrator.compose(brief, question_text, profile)
        trace.tool_calls.append(
            ToolCall(
                name="compose_answer",
                args={"narrator_backend": self.narrator.backend_name},
                result_summary=f"{len(answer_text)} chars",
                duration_ms=round((time.monotonic() - _compose_start) * 1000, 1),
            )
        )
        _verify_start = time.monotonic()
        report = run_safety_checks(answer_text, facts_for_grounding, allowed_dates)
        trace.tool_calls.append(
            ToolCall(
                name="verify_safety_checks",
                args={},
                result_summary="passed" if report.passed else f"failed: {'; '.join(c.name for c in report.failed_checks)}",
                ok=report.passed,
                duration_ms=round((time.monotonic() - _verify_start) * 1000, 1),
            )
        )

        used_fallback = False
        # Set from `report` (the *rejected* draft's report) below, before
        # `report` is reassigned to the mock narrator's own report -- this
        # must reflect why the fallback happened, not whether the mock
        # narrator's replacement text happens to pass every check too.
        disposition: _Disposition = "answered"
        if not report.passed and self.narrator.backend_name != "mock":
            # An LLM (or any non-mock) narrator failed a safety/grounding check.
            # Fall back to the deterministic narrator rather than return
            # unverified text. Keep the rejected draft and its failure
            # reasons -- requested after testing the Workbench, where a
            # fallback was visible but opaque (no way to see what the
            # draft said or specifically why it was rejected).
            #
            # The hard/soft split below is purely an observability signal
            # (see `AgentTrace.disposition`'s own docstring) -- it never
            # changes this fallback itself, which still fires on *any*
            # failed check exactly as before.
            disposition = "answered_after_hard_fallback" if report.has_hard_failure else "answered_after_soft_fallback"
            rejected_draft = answer_text
            rejected_report = report
            _fallback_compose_start = time.monotonic()
            answer_text = self._mock_narrator.compose(brief, question_text, profile)
            trace.tool_calls.append(
                ToolCall(
                    name="compose_fallback_answer",
                    args={"narrator_backend": self._mock_narrator.backend_name},
                    result_summary=f"{len(answer_text)} chars",
                    duration_ms=round((time.monotonic() - _fallback_compose_start) * 1000, 1),
                )
            )
            _fallback_verify_start = time.monotonic()
            report = run_safety_checks(answer_text, facts_for_grounding, allowed_dates)
            trace.tool_calls.append(
                ToolCall(
                    name="verify_safety_checks_fallback",
                    args={},
                    result_summary="passed" if report.passed else f"failed: {'; '.join(c.name for c in report.failed_checks)}",
                    ok=report.passed,
                    duration_ms=round((time.monotonic() - _fallback_verify_start) * 1000, 1),
                )
            )
            used_fallback = True
            # trace.narrator_backend was set above to the *selected*
            # backend (e.g. "bedrock") before we knew a fallback would
            # happen -- it must be corrected to "mock" here, since that's
            # what actually produced `answer_text`. An independent review
            # found that leaving it unchanged meant any consumer reading
            # only this field (not also checking for a `narrator_fallback`
            # entry in safety_checks) would wrongly conclude the real
            # model's output was returned. See docs/DECISIONS.md.
            trace.narrator_backend = self._mock_narrator.backend_name
            trace.rejected_draft = rejected_draft

        trace.safety_checks = list(report.checks)
        trace.disposition = disposition
        if used_fallback:
            failure_reasons = "; ".join(f"{c.name} ({c.detail})" if c.detail else c.name for c in rejected_report.failed_checks)
            trace.safety_checks.append(
                type(report.checks[0])(
                    name="narrator_fallback",
                    passed=True,
                    detail=(
                        f"Fell back to mock narrator because backend {self.narrator.backend_name!r} "
                        f"failed: {failure_reasons}. The rejected draft is in trace.rejected_draft."
                    ),
                )
            )

        trace.total_duration_ms = round((time.monotonic() - start_time) * 1000, 1)
        return AgentResponse(answer=answer_text, trace=trace, safe=report.passed)

    def ask_compound(
        self,
        user_id: str,
        question_text: str,
        planner: ToolPlanner,
        question_id: str | None = None,
        persona: str = "patient",
        on_stage: Callable[[str], None] | None = None,
        prior_grounded_facts: list[GroundedFact] | None = None,
        current_question: str | None = None,
    ) -> AgentResponse:
        """V2: answer a compound, multi-hop question via tool-calling
        (`orchestrator.run_compound_reasoning`) instead of `ask()`'s fixed
        5-intent classifier. Additive, not a replacement -- `ask()` is
        untouched by this method's existence (see `docs/DECISIONS.md`,
        2026-09-20 entry).

        `planner` is required, not defaulted: unlike `narrator`/`retriever`,
        there is no safe deterministic default that can plan an open-ended
        tool combination the way `MockNarrator` can safely template a fixed
        intent -- pass `tool_planner.BedrockToolPlanner()` for real use, or
        a scripted fake in tests.

        Red-flag detection runs first and is identical to `ask()`'s -- an
        emergency-phrasing question never reaches the tool-calling planner
        at all, the same unconditional, deterministic gate either pipeline
        goes through (see this project's own standing principle: emergency
        detection is never something an LLM's judgment call decides).

        `current_question` is used *only* for this red-flag `classify()`
        call, same as `ask()`'s own use -- the tool-calling `planner`
        itself (an LLM) keeps seeing the full, possibly context-wrapped
        `question_text`, since it can correctly reason about a "New
        question:" boundary the way a keyword classifier can't. See
        `ask()`'s own docstring for the full rationale.
        """
        start_time = time.monotonic()
        trace = AgentTrace(
            question_id=question_id,
            user_id=user_id,
            intent=COMPOUND_REASONING,
            narrator_backend=self.narrator.backend_name,
            retriever_backend=self.retriever.backend_name,
        )

        def stage(message: str) -> None:
            if on_stage is not None:
                on_stage(message)

        stage("Checking for emergency phrasing...")
        effective_current = current_question or question_text
        _classify_start = time.monotonic()
        intent_result = classify(effective_current)
        trace.tool_calls.append(
            ToolCall(
                name="classify_intent",
                args={"question_text": effective_current},
                result_summary=intent_result.intent,
                duration_ms=round((time.monotonic() - _classify_start) * 1000, 1),
            )
        )

        stage("Loading patient data...")
        _profile_start = time.monotonic()
        profile = self.data_store.get_user_profile(user_id)
        trace.tool_calls.append(
            ToolCall(
                name="get_user_profile",
                args={"user_id": user_id},
                result_summary=f"display_name={profile.display_name!r}",
                duration_ms=round((time.monotonic() - _profile_start) * 1000, 1),
            )
        )
        _bloodwork_start = time.monotonic()
        bloodwork = self.data_store.get_bloodwork(user_id)
        trace.tool_calls.append(
            ToolCall(
                name="get_bloodwork",
                args={"user_id": user_id},
                result_summary=f"latest_panel={'present' if bloodwork.latest_panel else 'missing'}",
                duration_ms=round((time.monotonic() - _bloodwork_start) * 1000, 1),
            )
        )
        questionnaire = self.data_store.get_questionnaire_context(user_id)

        allowed_dates: set[str] = {panel.measurement_date for panel in bloodwork.all_panels_newest_first()}

        if intent_result.intent == RED_FLAG:
            brief = Brief(intent=RED_FLAG)
            brief.red_flag = True
            brief.persona = persona
            trace.intent = RED_FLAG
            trace.grounded_facts = brief.grounded_facts
            trace.limitations = brief.limitations
            trace.retrieved_chunks = brief.retrieved_chunks
            stage("Emergency phrasing detected -- skipping tool planning.")
            return self._narrate_and_verify(brief, question_text, profile, trace, allowed_dates, start_time, prior_grounded_facts)

        ctx = ToolExecutionContext(
            profile=profile, bloodwork=bloodwork, questionnaire=questionnaire, catalog=self.catalog, retriever=self.retriever
        )
        brief, orchestrator_calls = run_compound_reasoning(question_text, ctx, planner, on_stage=on_stage)
        brief.persona = persona
        trace.tool_calls.extend(orchestrator_calls)
        # Independent review found this call missing entirely: a
        # tool-built Brief skipped both the staleness and source-value
        # plausibility checks `ask()` always runs, so a V2 answer could
        # report a stale or implausible value (e.g. LDL-C 5000 mg/dL)
        # with no limitation attached at all. See docs/DECISIONS.md,
        # 2026-09-21 entry.
        self._apply_source_data_checks(brief, trace, bloodwork)
        trace.grounded_facts = brief.grounded_facts
        trace.limitations = brief.limitations
        # Found live testing this exact path: `search_knowledge` correctly
        # retrieved real chunks (confirmed via the tool call's own
        # `result_summary`), but nothing copied `brief.retrieved_chunks`
        # onto `trace` the way `ask()` does -- so a real V2 retrieval
        # never reached the Workbench's evidence panel at all, even when
        # the tool worked correctly. See docs/DECISIONS.md, 2026-09-20.
        trace.retrieved_chunks = brief.retrieved_chunks

        # "Composing the answer..." covers both narration and the
        # independent safety verification that follows it inside
        # `_narrate_and_verify` -- there's no further real work after this
        # call returns, so no stage message belongs after it (one would
        # only ever fire simultaneously with the final result, not while
        # anything is actually still in progress).
        stage("Composing the answer...")
        return self._narrate_and_verify(brief, question_text, profile, trace, allowed_dates, start_time, prior_grounded_facts)
