"""Focused tests for mock_narrator.py's personalization summary.

Regression coverage for two bugs raised directly against a live
Workbench answer. First: the closing "Your questionnaire answers changed
this plan..." sentence used to be a single hardcoded string emitted
whenever *any* questionnaire modifier was present, unconditionally
claiming things about knee pain / sleep / stress regardless of which
modifiers actually fired. Second (found by a second independent review):
the fix for that still hardcoded which *sub-signal* fired within a topic
that can be triggered by more than one cause ("pacing" from sleep, stress,
or both; "nutrition"/"exercise_volume" were incorrectly combined into one
claim about "food and activity preferences" regardless of which of the
two actually fired). Both only coincidentally read correctly against the
shipped sample data, where every modifier and every sub-signal always
fires together.
"""

from care_agent.models import GroundedFact, QuestionnaireContext, QuestionnaireFact, UserProfile
from care_agent.narrator.mock_narrator import MockNarrator
from care_agent.reasoning import Brief, QuestionnaireModifier, build_questionnaire_modifiers

_PROFILE = UserProfile(user_id="u1", display_name="Test", age=None, sex=None, country=None)


def _modifier(topic: str) -> QuestionnaireModifier:
    return QuestionnaireModifier(
        topic=topic,
        text=f"{topic} text",
        grounded_fact=GroundedFact(claim=f"{topic} claim", source_type="questionnaire", source_ref=f"cautions.{topic}"),
    )


def _summary_line(answer: str) -> str | None:
    return next((line for line in answer.split("\n") if "questionnaire answers changed this plan" in line.lower()), None)


def test_personalization_summary_omitted_when_no_modifiers():
    brief = Brief(intent="priority_focus", questionnaire_modifiers=[])
    answer = MockNarrator().compose(brief, "what should I focus on?", _PROFILE)
    assert "questionnaire answers changed this plan" not in answer


def test_personalization_summary_only_mentions_modifiers_that_actually_fired():
    """Only a pacing modifier fired -- the summary must not also claim
    exercise/nutrition/family-history personalization happened."""
    brief = Brief(intent="priority_focus", questionnaire_modifiers=[_modifier("pacing")])
    answer = MockNarrator().compose(brief, "what should I focus on?", _PROFILE)
    assert _summary_line(answer) is not None
    assert "exercise limitation" not in answer
    assert "food and activity preferences" not in answer
    assert "family history" not in answer


def test_personalization_summary_only_mentions_exercise_when_only_exercise_fired():
    brief = Brief(intent="priority_focus", questionnaire_modifiers=[_modifier("exercise")])
    answer = MockNarrator().compose(brief, "what should I focus on?", _PROFILE)
    assert "exercise limitation" in answer
    assert "food and activity preferences" not in answer


def test_personalization_summary_pacing_names_only_the_sub_signal_that_fired():
    """Regression test: a second independent review found that even after
    the fix above, the pacing summary hardcoded "sleep and stress"
    regardless of whether both, or only one, actually triggered the
    modifier. Only stress reported (normal sleep) must not claim sleep
    was also reported."""
    context = QuestionnaireContext(user_id="u1", completed_at=None, facts=(QuestionnaireFact(field="mind.stress", value="high"),))
    brief = Brief(intent="priority_focus", questionnaire_modifiers=build_questionnaire_modifiers(context))
    answer = MockNarrator().compose(brief, "what should I focus on?", _PROFILE)
    summary = _summary_line(answer)
    assert summary is not None
    assert "high stress" in summary
    assert "sleep" not in summary


def test_personalization_summary_distinguishes_nutrition_from_exercise_volume():
    """Regression test: a second independent review found "nutrition" and
    "exercise_volume" were combined into one claim ("leans on your stated
    food and activity preferences") regardless of which one fired -- only
    low aerobic-activity volume reported (no nutrition signal) must not
    claim anything about food."""
    context = QuestionnaireContext(
        user_id="u1",
        completed_at=None,
        facts=(QuestionnaireFact(field="exercise.aerobic_activity", value="less_than_60_min_per_week"),),
    )
    brief = Brief(intent="priority_focus", questionnaire_modifiers=build_questionnaire_modifiers(context))
    answer = MockNarrator().compose(brief, "what should I focus on?", _PROFILE)
    summary = _summary_line(answer)
    assert summary is not None
    assert "aerobic activity" in summary
    assert "food" not in summary
