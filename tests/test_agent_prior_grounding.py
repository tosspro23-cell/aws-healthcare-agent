"""Cross-turn grounding: a number legitimately grounded in an *earlier*
turn of the same conversation must survive *this* turn's numeric_grounding
check when passed in as `prior_grounded_facts`, instead of being treated
as invented -- reproducing the real false-fallback found live (a narrator
correctly recalling an earlier-turn marker value while answering an
unrelated LDL/A1C follow-up). See docs/DECISIONS.md.

Uses HDL-C (47 mg/dL, "adequate" in the real sample data) as the
restated marker, not an abnormal one -- `ask()` (V1) always grounds every
*flagged* focus marker regardless of the question asked (it ranks focus
items over the whole panel, not just what the question names), so an
abnormal marker like triglycerides would already be grounded by V1's own
default gathering with no prior-turn facts involved at all, defeating the
point of this baseline. HDL-C is never a focus item, so V1's own
no-prior-facts baseline genuinely falls back on it too, matching V2's
narrower (only-what-was-explicitly-looked-up) gathering.

`prior_grounded_facts` only ever widens the grounding *check*; it must
never leak into `trace.grounded_facts` itself (the Evidence panel's own
"this turn's facts" list) -- covered below too.
"""

from __future__ import annotations

from care_agent.agent import HealthAgent
from care_agent.models import GroundedFact
from care_agent.orchestrator import PlannedToolCall, ToolPlan
from care_agent.reasoning import Brief

_PRIOR_HDL_SOURCE_REF = "panel_2026_05_06:hdl_c_mg_dl"


class _ScriptedPlanner:
    """Minimal local copy of `tests/test_orchestrator.py`'s own fake --
    not imported from there since `tests/` has no `__init__.py` and isn't
    a regular package (pytest's own rootdir-insertion convention here),
    so cross-test-file imports aren't the established pattern; every
    other fake in this suite (e.g. `_UnsafeFakeNarrator`) is likewise
    defined locally in the file that needs it."""

    backend_name = "fake"

    def __init__(self, plans: list[ToolPlan]):
        self._plans = list(plans)

    def propose_plan(self, question_text: str, repair_reason: str | None = None) -> ToolPlan:
        return self._plans.pop(0) if self._plans else ToolPlan(calls=())


class _RestatesOldNumberNarrator:
    backend_name = "fake_llm"

    def compose(self, brief: Brief, question_text: str, profile) -> str:
        return "Your earlier HDL-C reading was 47 mg/dL, which is still worth keeping an eye on."


def _prior_fact() -> GroundedFact:
    return GroundedFact(
        claim="HDL-C = 47 mg/dL (adequate) on 2026-05-06",
        source_type="bloodwork",
        source_ref=_PRIOR_HDL_SOURCE_REF,
        numeric_values=(47.0,),
        unit="mg/dL",
        display_name="HDL-C",
    )


# -- ask() (V1) ---------------------------------------------------------


def test_ask_falls_back_on_a_restated_number_with_no_prior_grounded_facts():
    """Baseline: reproduces the real bug exactly -- without prior-turn
    facts, this turn's own LDL-trend lookup never grounds 188, so the
    narrator's restated number is correctly treated as invented."""
    agent = HealthAgent(narrator=_RestatesOldNumberNarrator())
    response = agent.ask(user_id="user_demo_001", question_text="How is my LDL trending?")
    assert response.trace.disposition == "answered_after_soft_fallback"
    assert "47" not in response.answer


def test_ask_accepts_a_restated_number_when_prior_grounded_facts_supplied():
    agent = HealthAgent(narrator=_RestatesOldNumberNarrator())
    response = agent.ask(
        user_id="user_demo_001",
        question_text="How is my LDL trending?",
        prior_grounded_facts=[_prior_fact()],
    )
    assert response.trace.disposition == "answered"
    assert "47" in response.answer


def test_ask_prior_grounded_facts_do_not_leak_into_this_turns_grounded_facts():
    agent = HealthAgent(narrator=_RestatesOldNumberNarrator())
    response = agent.ask(
        user_id="user_demo_001",
        question_text="How is my LDL trending?",
        prior_grounded_facts=[_prior_fact()],
    )
    assert all(f.source_ref != _PRIOR_HDL_SOURCE_REF for f in response.trace.grounded_facts)


# -- ask_compound() (V2) -------------------------------------------------


def test_ask_compound_falls_back_on_a_restated_number_with_no_prior_grounded_facts():
    agent = HealthAgent(narrator=_RestatesOldNumberNarrator())
    planner = _ScriptedPlanner([ToolPlan(calls=(PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}),))])
    response = agent.ask_compound(user_id="user_demo_001", question_text="How is my LDL trending?", planner=planner)
    assert response.trace.disposition == "answered_after_soft_fallback"
    assert "47" not in response.answer


def test_ask_compound_accepts_a_restated_number_when_prior_grounded_facts_supplied():
    agent = HealthAgent(narrator=_RestatesOldNumberNarrator())
    planner = _ScriptedPlanner([ToolPlan(calls=(PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}),))])
    response = agent.ask_compound(
        user_id="user_demo_001",
        question_text="How is my LDL trending?",
        planner=planner,
        prior_grounded_facts=[_prior_fact()],
    )
    assert response.trace.disposition == "answered"
    assert "47" in response.answer
    assert all(f.source_ref != _PRIOR_HDL_SOURCE_REF for f in response.trace.grounded_facts)
