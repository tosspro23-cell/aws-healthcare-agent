"""Tests for `_naturalize_claim` and the paragraph-based rendering it
enables in `_compose_compound`/`_compose_general`'s grounded_facts
branch. Regression coverage for a real usability finding: the V2
(compound) soft-fallback answer read as an obvious "Here's what I
found:\\n- <claim>" template dump, visibly different in style from what
the real Bedrock narrator wrote for the identical facts -- defeating the
point of a safety-net fallback that's supposed to be indistinguishable
in *quality* from the primary path, even though it's always 100%
grounded by construction either way. See docs/DECISIONS.md, 2026-09-29
entry.
"""

from __future__ import annotations

from care_agent.intent import COMPOUND_REASONING
from care_agent.models import GroundedFact, UserProfile
from care_agent.narrator.mock_narrator import MockNarrator, _naturalize_claim
from care_agent.reasoning import Brief

_PROFILE = UserProfile(user_id="u1", display_name="Test", age=None, sex=None, country=None)


def test_naturalize_claim_renders_a_snapshot_as_a_sentence():
    assert _naturalize_claim("LDL-C = 162 mg/dL (high) on 2026-05-06") == "Your LDL-C was 162 mg/dL (high) on 2026-05-06."


def test_naturalize_claim_renders_a_trend_as_a_sentence():
    claim = "LDL-C trend: 148 mg/dL on 2025-12-08 -> 162 mg/dL on 2026-05-06 (up)"
    assert _naturalize_claim(claim) == "Your LDL-C has increased from 148 mg/dL on 2025-12-08 to 162 mg/dL on 2026-05-06."


def test_naturalize_claim_renders_a_downward_trend_correctly():
    claim = "HDL-C trend: 55 mg/dL on 2025-12-08 -> 48 mg/dL on 2026-05-06 (down)"
    assert _naturalize_claim(claim) == "Your HDL-C has decreased from 55 mg/dL on 2025-12-08 to 48 mg/dL on 2026-05-06."


def test_naturalize_claim_renders_an_implausible_value_without_alarming_language():
    claim = "LDL-C = 50000 mg/dL (flagged implausible) on 2026-05-06"
    result = _naturalize_claim(claim)
    assert "50000 mg/dL" in result
    assert "2026-05-06" in result
    assert "data-entry error" in result


def test_naturalize_claim_humanizes_the_dotted_questionnaire_field_name():
    """Regression test: the raw dotted internal identifier
    ("nutrition.sugary_foods") used to leak straight into the composed
    prose ("for nutrition.sugary_foods"), reading as code, not a
    sentence."""
    claim = "Questionnaire nutrition.sugary_foods: 3-4 days per week"
    assert _naturalize_claim(claim) == "You reported 3-4 days per week for sugary foods."


def test_naturalize_claim_renders_allergies_as_a_sentence():
    assert _naturalize_claim("Reported allergies: shellfish, peanuts") == "You have reported allergies to shellfish, peanuts."


def test_naturalize_claim_falls_through_unrecognized_shapes_unchanged_except_punctuation():
    """A claim shape this function doesn't specifically recognize (e.g. a
    free-text questionnaire-modifier claim, already reasonably natural)
    must never be dropped or mangled -- only capitalized and given a
    trailing period if it lacks one."""
    assert _naturalize_claim("user reports levothyroxine use") == "User reports levothyroxine use."
    assert _naturalize_claim("Already has a period.") == "Already has a period."


def test_naturalize_claim_never_crashes_on_empty_input():
    assert _naturalize_claim("") == ""


def test_compose_compound_reads_as_flowing_prose_not_a_bulleted_dump():
    """The actual end-to-end regression: no "Here's what I found:"
    preamble, no leading "- " bullet dashes -- the facts read as
    sentences in a paragraph, the same shape a real LLM narrator's
    rephrasing has."""
    brief = Brief(intent=COMPOUND_REASONING)
    brief.grounded_facts = [
        GroundedFact(
            claim="LDL-C trend: 148 mg/dL on 2025-12-08 -> 162 mg/dL on 2026-05-06 (up)",
            source_type="bloodwork",
            source_ref="trend:ldl_c_mg_dl",
            numeric_values=(162.0, 148.0),
            unit="mg/dL",
            display_name="LDL-C",
        ),
        GroundedFact(
            claim="HbA1c trend: 5.8 % on 2025-12-08 -> 6.1 % on 2026-05-06 (up)",
            source_type="bloodwork",
            source_ref="trend:hba1c_percent",
            numeric_values=(6.1, 5.8),
            unit="%",
            display_name="HbA1c",
        ),
    ]
    answer = MockNarrator().compose(brief, "Compare my LDL and A1C trends.", _PROFILE)
    assert "Here's what I found" not in answer
    assert "\n- " not in answer
    assert "Your LDL-C has increased from 148 mg/dL on 2025-12-08 to 162 mg/dL on 2026-05-06." in answer
    assert "Your HbA1c has increased from 5.8 % on 2025-12-08 to 6.1 % on 2026-05-06." in answer
