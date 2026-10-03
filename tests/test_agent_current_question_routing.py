"""`current_question` routing: a follow-up's deterministic intent
classification must be based on the bare, live question, never on the
prior-turn Q&A text the Workbench folds into `question_text` for the
narrator's own continuity.

Reproduces the exact live false-route an independent review found:
`classify("Is my LDL getting worse?")` alone is `trend_check`, but the
same question wrapped in a prior "What should I focus on first" turn's
own Q&A (precisely what `Workbench.tsx`'s `buildContextualQuestion`
sends) came back as `priority_focus` instead -- the old answer's own
"focus"/"first" keywords hijacked routing for an unrelated new question.
See docs/DECISIONS.md.
"""

from __future__ import annotations

from care_agent.intent import PRIORITY_FOCUS, TREND_CHECK
from care_agent.orchestrator import PlannedToolCall, ToolPlan

_PRIOR_QUESTION = "What should I focus on first in my results?"
_PRIOR_ANSWER = "Your top priority is LDL-C, which is high at 162 mg/dL. Focus on this first alongside lifestyle changes."
_NEW_QUESTION = "Is my LDL getting worse?"
_CONTEXT_WRAPPED_TEXT = (
    "Context from earlier in this conversation, for continuity only -- base any new numeric claims on your own "
    "fresh data lookups for this question, not by repeating earlier figures unless they're reconfirmed now:\n\n"
    f"Q: {_PRIOR_QUESTION}\nA: {_PRIOR_ANSWER}\n\nNew question: {_NEW_QUESTION}"
)


class _ScriptedPlanner:
    backend_name = "fake"

    def propose_plan(self, question_text: str, repair_reason: str | None = None) -> ToolPlan:
        return ToolPlan(calls=(PlannedToolCall("get_marker_trend", {"concept_id": "ldl_c_mg_dl"}),))


def test_ask_without_current_question_reproduces_the_hijacked_route(agent):
    """Baseline, confirming the bug this fix closes actually exists: with
    no `current_question` supplied, `ask()` falls back to classifying the
    full context-wrapped blob -- exactly today's (buggy) behavior."""
    response = agent.ask(user_id="user_demo_001", question_text=_CONTEXT_WRAPPED_TEXT)
    assert response.trace.intent == PRIORITY_FOCUS


def test_ask_with_current_question_routes_on_the_bare_live_question(agent):
    """The fix: passing the bare `current_question` alongside the full
    context-wrapped `question_text` routes correctly, regardless of what
    keywords the folded-in history happens to contain."""
    response = agent.ask(
        user_id="user_demo_001",
        question_text=_CONTEXT_WRAPPED_TEXT,
        current_question=_NEW_QUESTION,
    )
    assert response.trace.intent == TREND_CHECK


_STALE_EMERGENCY_CONTEXT = (
    "Context from earlier in this conversation, for continuity only:\n\n"
    "Q: I have chest pain, what should I do?\nA: Please seek emergency care immediately.\n\n"
    "New question: What does my cholesterol panel look like?"
)


def test_ask_compound_without_current_question_reproduces_a_stale_red_flag_misfire(agent):
    """A more consequential version of the same bug: a *resolved* earlier
    turn's emergency phrasing, now just inert history, re-triggers the
    red-flag gate for a completely unrelated, benign follow-up -- routing
    a mundane "what does my panel look like" question to the emergency
    branch instead of the tool-calling planner."""
    response = agent.ask_compound(user_id="user_demo_001", question_text=_STALE_EMERGENCY_CONTEXT, planner=_ScriptedPlanner())
    assert response.trace.intent == "red_flag_emergency"


def test_ask_compound_with_current_question_does_not_misfire_on_stale_history(agent):
    """The fix: the red-flag gate checks only the bare live question, so
    stale emergency phrasing from an earlier, already-resolved turn can
    no longer hijack routing for a new, unrelated question."""
    response = agent.ask_compound(
        user_id="user_demo_001",
        question_text=_STALE_EMERGENCY_CONTEXT,
        planner=_ScriptedPlanner(),
        current_question="What does my cholesterol panel look like?",
    )
    assert response.trace.intent == "compound_reasoning"
