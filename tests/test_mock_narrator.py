"""Focused tests for mock_narrator.py's personalization summary.

Regression coverage for a bug raised directly against a live Workbench
answer: the closing "Your questionnaire answers changed this plan..."
sentence used to be a single hardcoded string emitted whenever *any*
questionnaire modifier was present, unconditionally claiming things about
knee pain / sleep / stress regardless of which modifiers actually fired.
Only coincidentally correct against the shipped sample data, where every
modifier always fires together.
"""

from care_agent.models import GroundedFact, UserProfile
from care_agent.narrator.mock_narrator import MockNarrator
from care_agent.reasoning import Brief, QuestionnaireModifier

_PROFILE = UserProfile(user_id="u1", display_name="Test", age=None, sex=None, country=None)


def _modifier(topic: str) -> QuestionnaireModifier:
    return QuestionnaireModifier(
        topic=topic,
        text=f"{topic} text",
        grounded_fact=GroundedFact(claim=f"{topic} claim", source_type="questionnaire", source_ref=f"cautions.{topic}"),
    )


def test_personalization_summary_omitted_when_no_modifiers():
    brief = Brief(intent="priority_focus", questionnaire_modifiers=[])
    answer = MockNarrator().compose(brief, "what should I focus on?", _PROFILE)
    assert "questionnaire answers changed this plan" not in answer


def test_personalization_summary_only_mentions_modifiers_that_actually_fired():
    """Only a pacing modifier fired -- the summary must not also claim
    exercise/nutrition/family-history personalization happened."""
    brief = Brief(intent="priority_focus", questionnaire_modifiers=[_modifier("pacing")])
    answer = MockNarrator().compose(brief, "what should I focus on?", _PROFILE)
    assert "questionnaire answers changed this plan" in answer
    assert "sleep and stress" in answer
    assert "exercise limitation" not in answer
    assert "food and activity preferences" not in answer
    assert "family history" not in answer


def test_personalization_summary_only_mentions_exercise_when_only_exercise_fired():
    brief = Brief(intent="priority_focus", questionnaire_modifiers=[_modifier("exercise")])
    answer = MockNarrator().compose(brief, "what should I focus on?", _PROFILE)
    assert "exercise limitation" in answer
    assert "sleep and stress" not in answer
