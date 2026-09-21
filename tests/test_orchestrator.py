"""Tests for the V2 tool-calling compound-reasoning engine.

Real sample data throughout (`user_demo_001`): LDL-C 148->162 mg/dL and
HbA1c 5.8->6.1% both trend up between the two shipped panels, and
`nutrition.sugary_foods` is a real questionnaire field -- exactly the
"compare two marker trends against a reported diet change" compound
question class this engine exists to answer, which the fixed 5-intent
classifier structurally cannot (it can only trigger one intent, on one
marker, per question).
"""

from __future__ import annotations

from care_agent.agent import HealthAgent
from care_agent.catalog import DEFAULT_CATALOG_PATH, BiomarkerCatalog
from care_agent.data_store import DEFAULT_DATA_DIR, DataStore
from care_agent.orchestrator import (
    PlannedToolCall,
    ToolExecutionContext,
    ToolPlan,
    capability_gate,
    execute_tool_call,
    run_compound_reasoning,
)
from care_agent.reasoning import Brief
from care_agent.retrieval import DEFAULT_KB_PATH, KnowledgeRetriever


def _real_ctx(user_id: str = "user_demo_001") -> ToolExecutionContext:
    store = DataStore(DEFAULT_DATA_DIR)
    return ToolExecutionContext(
        profile=store.get_user_profile(user_id),
        bloodwork=store.get_bloodwork(user_id),
        questionnaire=store.get_questionnaire_context(user_id),
        catalog=BiomarkerCatalog(DEFAULT_CATALOG_PATH),
        retriever=KnowledgeRetriever(kb_path=DEFAULT_KB_PATH),
    )


# -- capability_gate ---------------------------------------------------------


def test_capability_gate_accepts_known_tool_and_concept():
    allowed, reason = capability_gate(PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}))
    assert allowed is True
    assert reason is None


def test_capability_gate_rejects_unknown_tool():
    allowed, reason = capability_gate(PlannedToolCall("delete_patient_record", {}))
    assert allowed is False
    assert "Unknown tool" in reason


def test_capability_gate_rejects_out_of_vocabulary_concept_id():
    """The whole point of the gate: the planner cannot invent a marker
    this system has no catalog/plausibility coverage for."""
    allowed, reason = capability_gate(PlannedToolCall("get_marker_trend", {"concept_id": "made_up_marker_xyz"}))
    assert allowed is False
    assert "Unsupported concept_id" in reason


def test_capability_gate_rejects_empty_search_query():
    allowed, reason = capability_gate(PlannedToolCall("search_knowledge", {"query": ""}))
    assert allowed is False


def test_capability_gate_rejects_questionnaire_fact_missing_its_required_field_argument():
    """Regression test: an independent review found `get_questionnaire_fact({})`
    (missing its required `field` argument) passed this gate cleanly and
    then crashed `execute_tool_call` with a bare `KeyError('field')`,
    entirely bypassing the planner's own repair path -- the gate had
    bespoke checks for `concept_id` and `query` but nothing generic, so
    any other tool's required argument went unchecked."""
    allowed, reason = capability_gate(PlannedToolCall("get_questionnaire_fact", {}))
    assert allowed is False
    assert "field" in reason


def test_capability_gate_accepts_zero_argument_tools_with_no_args():
    for tool_name in ("get_focus_markers", "get_supplement_cautions", "get_allergies"):
        allowed, reason = capability_gate(PlannedToolCall(tool_name, {}))
        assert allowed is True, f"{tool_name}: {reason}"


# -- individual tool executors, against real sample data ---------------------


def test_execute_get_marker_trend_reports_real_direction():
    result = execute_tool_call(PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}), _real_ctx())
    assert result.ok is True
    assert len(result.grounded_facts) == 1
    fact = result.grounded_facts[0]
    assert 162.0 in fact.numeric_values and 148.0 in fact.numeric_values
    assert "up" in result.result_summary


def test_execute_get_marker_snapshot_grounds_the_real_value():
    result = execute_tool_call(PlannedToolCall("get_marker_snapshot", {"concept_id": "hba1c_percent"}), _real_ctx())
    assert result.grounded_facts[0].numeric_values == (6.1,)
    assert "hba1c_percent" in result.mentioned_markers


def test_execute_get_questionnaire_fact_real_field():
    result = execute_tool_call(PlannedToolCall("get_questionnaire_fact", {"field": "nutrition.sugary_foods"}), _real_ctx())
    assert result.ok is True
    assert result.grounded_facts
    assert not result.limitations


def test_execute_get_questionnaire_fact_missing_field_is_a_limitation_not_a_crash():
    result = execute_tool_call(PlannedToolCall("get_questionnaire_fact", {"field": "made_up_field"}), _real_ctx())
    assert result.ok is True
    assert not result.grounded_facts
    assert result.limitations[0].kind == "missing_data"


def test_execute_get_focus_markers_returns_only_abnormal_markers():
    result = execute_tool_call(PlannedToolCall("get_focus_markers", {}), _real_ctx())
    concept_ids = {f.source_ref.split(":")[1] for f in result.grounded_facts}
    assert "ldl_c_mg_dl" in concept_ids  # real data: classified "high"
    assert "hdl_c_mg_dl" not in concept_ids  # real data: classified "adequate"


# -- run_compound_reasoning: the planning + capability-gate + execution loop -


class _ScriptedPlanner:
    """Returns pre-scripted plans in sequence -- one per call to
    `propose_plan` -- mirroring this project's `_UnsafeFakeNarrator`-style
    fakes and SPEC-M16's own `FakeModelProvider.tool_call` convention: a
    test double that proves the surrounding pipeline, not "is the model
    smart," which is validated live/manually instead (see every other LLM
    backend in this project)."""

    backend_name = "fake"

    def __init__(self, plans: list[ToolPlan]):
        self._plans = list(plans)
        self.calls: list[str | None] = []

    def propose_plan(self, question_text: str, repair_reason: str | None = None) -> ToolPlan:
        self.calls.append(repair_reason)
        return self._plans.pop(0) if self._plans else ToolPlan(calls=())


def test_run_compound_reasoning_answers_a_real_compound_question():
    planner = _ScriptedPlanner(
        [
            ToolPlan(
                calls=(
                    PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}),
                    PlannedToolCall("get_marker_trend", {"concept_id": "hba1c_percent"}),
                    PlannedToolCall("get_questionnaire_fact", {"field": "nutrition.sugary_foods"}),
                )
            )
        ]
    )
    brief, trace_calls = run_compound_reasoning(
        "Compare my LDL and A1C trends and tell me if my reported diet change seems to be helping.", _real_ctx(), planner
    )
    assert isinstance(brief, Brief)
    all_values = {v for fact in brief.grounded_facts for v in fact.numeric_values}
    assert {148.0, 162.0, 5.8, 6.1}.issubset(all_values)
    assert any(c.name == "get_marker_trend" for c in trace_calls)
    assert not brief.limitations  # nothing here should be missing for this user


def test_run_compound_reasoning_discloses_a_partially_rejected_plan():
    """Regression test: an independent review found that when a plan
    mixes one legal call and one illegal call, the legal call's success
    made the loop break immediately -- the illegal call's rejection was
    used for nothing, never became a repair attempt and never became a
    `Limitation` either. Fixed in two steps (see docs/DECISIONS.md,
    2026-09-21 entries): first, disclosure (the rejection always becomes
    a `Limitation`); then, this test's own scripted planner has no
    second plan to offer, so the repair attempt below finds nothing new
    and the original rejection is still what gets disclosed -- proving
    the "planner gives up on repair" path doesn't silently lose it
    either."""
    planner = _ScriptedPlanner(
        [
            ToolPlan(
                calls=(
                    PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}),
                    PlannedToolCall("get_marker_trend", {"concept_id": "made_up_marker"}),
                )
            )
        ]
    )
    brief, _ = run_compound_reasoning("What's my LDL trend, and what about this other marker?", _real_ctx(), planner)
    assert brief.grounded_facts  # the legal half still answered
    assert len(planner.calls) == 2  # a repair attempt for the rejected half now genuinely happens
    assert any(lim.kind == "partial_tool_rejection" and "made_up_marker" in lim.detail for lim in brief.limitations)


def test_run_compound_reasoning_repairs_the_rejected_half_of_a_mixed_plan():
    """The actual enhancement: unlike the disclosure-only test above
    (whose scripted planner has nothing left to offer), a repair round
    that *does* fix the rejected half must be used -- both calls' facts
    end up in the Brief, no `partial_tool_rejection` limitation remains,
    and the already-accepted call from round one is never re-executed
    (asserted via `trace_calls` containing exactly one execution per
    tool, not two, even though the repair round's own plan re-proposes
    the already-accepted `hba1c_percent` call alongside the fixed one)."""
    planner = _ScriptedPlanner(
        [
            ToolPlan(
                calls=(
                    PlannedToolCall("get_marker_trend", {"concept_id": "hba1c_percent"}),
                    PlannedToolCall("get_marker_trend", {"concept_id": "made_up_marker"}),
                )
            ),
            ToolPlan(
                calls=(
                    PlannedToolCall("get_marker_trend", {"concept_id": "hba1c_percent"}),
                    PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}),
                )
            ),
        ]
    )
    brief, trace_calls = run_compound_reasoning("What's my LDL and A1C trend?", _real_ctx(), planner)

    assert len(planner.calls) == 2
    assert not any(lim.kind == "partial_tool_rejection" for lim in brief.limitations)
    all_values = {v for fact in brief.grounded_facts for v in fact.numeric_values}
    assert {5.8, 6.1}.issubset(all_values)  # hba1c_percent, from round one
    assert {148.0, 162.0}.issubset(all_values)  # ldl_c_mg_dl, from the repaired round two

    execution_calls = [c for c in trace_calls if c.name == "get_marker_trend"]
    assert len(execution_calls) == 2  # hba1c_percent executed once, not twice


def test_run_compound_reasoning_repairs_after_a_rejected_call():
    """The bounded-repair path: a first plan referencing an unsupported
    concept_id is rejected by the capability gate, and the planner gets
    exactly one corrected attempt."""
    planner = _ScriptedPlanner(
        [
            ToolPlan(calls=(PlannedToolCall("get_marker_trend", {"concept_id": "made_up_marker"}),)),
            ToolPlan(calls=(PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}),)),
        ]
    )
    brief, _ = run_compound_reasoning("What's my LDL trend?", _real_ctx(), planner)
    assert brief.grounded_facts  # the repaired plan's call succeeded
    assert len(planner.calls) == 2
    assert planner.calls[0] is None  # first attempt: no repair reason yet
    assert planner.calls[1] is not None and "made_up_marker" in planner.calls[1]  # second attempt: told exactly why


def test_run_compound_reasoning_repairs_after_a_call_missing_its_required_argument():
    """Regression test: before capability_gate validated required
    arguments generically, a planned `get_questionnaire_fact` call with
    no `field` argument at all was accepted by the gate and then crashed
    `execute_tool_call` with a bare `KeyError` -- an independent review
    found this bypassed the repair path entirely instead of producing a
    rejection the planner could act on. This must now behave exactly
    like any other rejected call: a bounded repair attempt, not a
    crash."""
    planner = _ScriptedPlanner(
        [
            ToolPlan(calls=(PlannedToolCall("get_questionnaire_fact", {}),)),
            ToolPlan(calls=(PlannedToolCall("get_questionnaire_fact", {"field": "nutrition.sugary_foods"}),)),
        ]
    )
    brief, _ = run_compound_reasoning("What did I report about sugary foods?", _real_ctx(), planner)
    assert brief.grounded_facts  # the repaired plan's call succeeded
    assert len(planner.calls) == 2
    assert planner.calls[1] is not None and "field" in planner.calls[1]


def test_run_compound_reasoning_gives_up_honestly_when_repair_also_fails():
    """Bounded, not silent and not an infinite loop: two rejected plans in
    a row (MAX_ITERATIONS=2) ends in an honest, disclosed limitation --
    never a crash, never a guessed answer."""
    planner = _ScriptedPlanner(
        [
            ToolPlan(calls=(PlannedToolCall("get_marker_trend", {"concept_id": "made_up_marker_1"}),)),
            ToolPlan(calls=(PlannedToolCall("get_marker_trend", {"concept_id": "made_up_marker_2"}),)),
        ]
    )
    brief, _ = run_compound_reasoning("What's my made-up marker doing?", _real_ctx(), planner)
    assert not brief.grounded_facts
    assert brief.limitations[0].kind == "unsupported_request"
    assert len(planner.calls) == 2  # bounded: exactly MAX_ITERATIONS attempts, not more


# -- HealthAgent.ask_compound: the full pipeline, including the shared safety gate --


def test_ask_compound_answers_safely_end_to_end():
    agent = HealthAgent()
    planner = _ScriptedPlanner([ToolPlan(calls=(PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}),))])
    response = agent.ask_compound(user_id="user_demo_001", question_text="How is my LDL trending?", planner=planner)
    assert response.safe is True
    assert "162" in response.answer and "148" in response.answer


def test_run_compound_reasoning_retrieves_automatically_when_planner_did_not_search():
    """Regression test: found live testing the browser demo across ~10
    real runs, not a single unit-test scenario -- the real Bedrock
    planner reliably chose not to call `search_knowledge` for
    data-lookup-shaped questions, so V2 never surfaced the project's own
    vetted knowledge base at all, unlike V1 which always retrieves. This
    is the deterministic fallback, mirroring `ask()`'s own unconditional
    retrieval step (`CONCEPT_TOPIC_TAGS`-scoped) rather than relying on
    the planner to remember."""
    planner = _ScriptedPlanner([ToolPlan(calls=(PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}),))])
    brief, trace_calls = run_compound_reasoning("How is my LDL trending?", _real_ctx(), planner)
    assert brief.retrieved_chunks  # the real KB has real LDL-C content (kb_lipid_*)
    assert any(c.name == "retrieve_knowledge" for c in trace_calls)


def test_run_compound_reasoning_does_not_duplicate_retrieval_when_planner_already_searched():
    """The other half: when the planner *did* explicitly call
    `search_knowledge` with its own tailored query, the automatic
    fallback must not run a second, redundant retrieval on top of it."""
    planner = _ScriptedPlanner([ToolPlan(calls=(PlannedToolCall("search_knowledge", {"query": "LDL cholesterol"}),))])
    brief, trace_calls = run_compound_reasoning("How is my LDL trending?", _real_ctx(), planner)
    retrieve_calls = [c for c in trace_calls if c.name in {"search_knowledge", "retrieve_knowledge"}]
    assert len(retrieve_calls) == 1
    assert retrieve_calls[0].name == "search_knowledge"  # the planner's own explicit call, not the fallback


def test_ask_compound_retrieved_chunks_reach_the_trace():
    """Regression test: found live testing the browser demo, not by
    inspection. `search_knowledge` correctly retrieved real chunks
    (confirmed via the tool call's own result_summary), but nothing
    copied `brief.retrieved_chunks` onto `trace` the way `ask()` does --
    so a real V2 retrieval never reached the Workbench's evidence panel
    at all, even when the tool worked correctly."""
    agent = HealthAgent()
    planner = _ScriptedPlanner([ToolPlan(calls=(PlannedToolCall("search_knowledge", {"query": "LDL cholesterol lifestyle"}),))])
    response = agent.ask_compound(user_id="user_demo_001", question_text="What helps with high LDL?", planner=planner)
    assert response.safe is True
    assert len(response.trace.retrieved_chunks) > 0


def test_ask_compound_shows_every_gathered_fact_category_not_just_one():
    """Regression test: found live, not by inspection. A supplement-safety
    question gathering facts from three different tools (cautions,
    allergies, a marker snapshot) used to render only the marker snapshot
    -- `_compose_general`'s mentioned_markers/focus_items/grounded_facts
    branches are mutually exclusive, so populating `mentioned_markers`
    (from get_marker_snapshot) silently hid the allergy fact even though
    it was correctly grounded and present in the trace. Fixed with a
    dedicated `_compose_compound` template that always shows everything
    gathered (see docs/DECISIONS.md, 2026-09-20 entry)."""
    agent = HealthAgent()
    planner = _ScriptedPlanner(
        [
            ToolPlan(
                calls=(
                    PlannedToolCall("get_allergies", {}),
                    PlannedToolCall("get_marker_snapshot", {"concept_id": "vitamin_d_25oh_ng_ml"}),
                )
            )
        ]
    )
    response = agent.ask_compound(
        user_id="user_demo_001", question_text="Is a vitamin D supplement safe given my allergies?", planner=planner
    )
    assert response.safe is True
    assert "shellfish" in response.answer.lower()  # the allergy fact
    assert "vitamin d" in response.answer.lower()  # the marker snapshot fact


def test_ask_compound_clinician_persona_changes_disclaimer_not_facts():
    agent = HealthAgent()
    planner = _ScriptedPlanner([ToolPlan(calls=(PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}),))])
    response = agent.ask_compound(user_id="user_demo_001", question_text="How is my LDL trending?", planner=planner, persona="clinician")
    assert response.safe is True
    assert "162" in response.answer and "148" in response.answer  # facts unchanged
    assert "decision-support" in response.answer.lower()
    assert "not a diagnosis or treatment plan" not in response.answer  # patient-only wording gone


def test_ask_compound_on_stage_reports_real_progress_in_order():
    """The mechanism behind live progress display in the Workbench (no
    token streaming needed -- see docs/DECISIONS.md, 2026-09-20 entry):
    coarse-grained stage messages fire in the real order work happens,
    not all at once at the end."""
    agent = HealthAgent()
    planner = _ScriptedPlanner([ToolPlan(calls=(PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}),))])
    stages: list[str] = []
    response = agent.ask_compound(user_id="user_demo_001", question_text="How is my LDL trending?", planner=planner, on_stage=stages.append)
    assert response.safe is True
    assert stages == [
        "Checking for emergency phrasing...",
        "Loading patient data...",
        "Planning which tools to call...",
        "Calling tools: get_marker_trend...",
        "Searching the knowledge base...",
        "Composing the answer...",
    ]


def test_ask_compound_on_stage_is_optional():
    """Every existing/future caller that doesn't pass `on_stage` is
    unaffected -- it defaults to doing nothing, not a required wiring."""
    agent = HealthAgent()
    planner = _ScriptedPlanner([ToolPlan(calls=(PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}),))])
    response = agent.ask_compound(user_id="user_demo_001", question_text="How is my LDL trending?", planner=planner)
    assert response.safe is True


def test_ask_compound_red_flag_never_reaches_the_planner():
    """The unconditional gate: an emergency-phrased question is caught by
    the same deterministic `classify()` check `ask()` uses, before the
    tool-calling planner is ever invoked -- proven here by the planner
    never being called at all, not just by the answer's content."""
    agent = HealthAgent()
    planner = _ScriptedPlanner([ToolPlan(calls=())])
    response = agent.ask_compound(user_id="user_demo_001", question_text="I have chest pain, what should I do?", planner=planner)
    assert response.safe is True
    assert planner.calls == []  # never invoked


class _UnsafeFakeNarratorForV2:
    backend_name = "fake_llm"

    def compose(self, brief, question_text, profile) -> str:
        return "You definitely have diabetes. Take 500 mg of metformin twice daily."


def test_ask_compound_shares_the_same_safety_gate_as_ask():
    """The central claim this whole engine rests on: V2-built Briefs go
    through the *identical* narrate-verify-fallback gate as V1's, not a
    separate or weaker one. An unsafe narrator draft gets caught and
    replaced here exactly like `test_unsafe_llm_output_triggers_fallback_to_mock`
    already proves for `ask()`."""
    agent = HealthAgent(narrator=_UnsafeFakeNarratorForV2())
    planner = _ScriptedPlanner([ToolPlan(calls=(PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}),))])
    response = agent.ask_compound(user_id="user_demo_001", question_text="How is my LDL trending?", planner=planner)
    assert response.safe is True
    assert "you definitely have diabetes" not in response.answer.lower()
    assert "500 mg" not in response.answer.lower()
    assert response.trace.disposition == "answered_after_hard_fallback"
