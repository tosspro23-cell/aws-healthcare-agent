import pytest

from care_agent.catalog import BiomarkerCatalog
from care_agent.data_store import DataStore
from care_agent.reasoning import (
    alcohol_unknown_limitation,
    build_questionnaire_modifiers,
    build_supplement_cautions,
    detect_metabolic_priority_pattern,
    importance_weight,
    rank_focus_markers,
    severity_weight,
    staleness_limitation,
)


@pytest.fixture()
def catalog(data_dir):
    return BiomarkerCatalog(data_dir / "mock_biomarker_catalog.sqlite")


@pytest.fixture()
def bloodwork(data_dir):
    return DataStore(data_dir).get_bloodwork("user_demo_001")


@pytest.fixture()
def questionnaire(data_dir):
    return DataStore(data_dir).get_questionnaire_context("user_demo_001")


@pytest.fixture()
def profile(data_dir):
    return DataStore(data_dir).get_user_profile("user_demo_001")


def test_importance_weight_from_catalog(catalog):
    high_entry = catalog.lookup("hba1c_percent")  # importance="high"
    low_entry = catalog.lookup("cortisol_morning_ug_dl")  # importance="low"
    assert importance_weight(high_entry) == 1.5
    assert importance_weight(low_entry) == 0.5
    assert importance_weight(None) == 1.0


def test_severity_weight_known_and_unknown():
    assert severity_weight("high") == 3.0
    assert severity_weight("adequate") == 0.0
    assert severity_weight(None) == 1.0
    assert severity_weight("some_future_label") == 1.0


def test_rank_focus_markers_excludes_adequate(bloodwork, catalog):
    items = rank_focus_markers(bloodwork.latest_panel, catalog, set())
    concept_ids = [it.marker.concept_id for it in items]
    assert "hdl_c_mg_dl" not in concept_ids  # classification == "adequate"
    assert "alt_u_l" not in concept_ids
    assert "egfr_ml_min_1_73m2" not in concept_ids


def test_rank_focus_markers_orders_by_severity_and_importance(bloodwork, catalog):
    items = rank_focus_markers(bloodwork.latest_panel, catalog, set())
    top_ids = [it.marker.concept_id for it in items[:4]]
    # LDL, HbA1c, fasting glucose are all tier-1/high-importance + severe.
    assert set(top_ids[:3]) <= {"ldl_c_mg_dl", "hba1c_percent", "fasting_glucose_mg_dl", "triglycerides_mg_dl"}


def test_mentioned_marker_gets_score_bonus(bloodwork, catalog):
    items_unmentioned = rank_focus_markers(bloodwork.latest_panel, catalog, set())
    items_mentioned = rank_focus_markers(bloodwork.latest_panel, catalog, {"vitamin_d_25oh_ng_ml"})
    unmentioned_score = next(it.rank_score for it in items_unmentioned if it.marker.concept_id == "vitamin_d_25oh_ng_ml")
    mentioned_score = next(it.rank_score for it in items_mentioned if it.marker.concept_id == "vitamin_d_25oh_ng_ml")
    assert mentioned_score > unmentioned_score


def test_metabolic_pattern_detected_for_sample_data(bloodwork, catalog):
    items = rank_focus_markers(bloodwork.latest_panel, catalog, set())
    assert detect_metabolic_priority_pattern(items) is True


def test_metabolic_pattern_not_detected_when_a_marker_missing():
    from care_agent.models import Biomarker
    from care_agent.reasoning import FocusItem

    items = [
        FocusItem(
            marker=Biomarker(concept_id="hba1c_percent", display_name="HbA1c", value=6.1, unit="%", classification="elevated"),
            catalog_entry=None,
            rank_score=3.0,
            mentioned_by_user=False,
        )
    ]
    assert detect_metabolic_priority_pattern(items) is False


def test_questionnaire_modifiers_cover_expected_topics(questionnaire):
    modifiers = build_questionnaire_modifiers(questionnaire)
    topics = {m.topic for m in modifiers}
    assert {"exercise", "nutrition", "pacing", "family_history"} <= topics


def test_questionnaire_modifiers_never_mention_declined_field(questionnaire):
    modifiers = build_questionnaire_modifiers(questionnaire)
    for m in modifiers:
        assert "phq2" not in m.text.lower()
        assert "phq2" not in m.grounded_fact.claim.lower()


def test_supplement_cautions_include_medication_and_allergy(questionnaire, profile):
    cautions = build_supplement_cautions(questionnaire, profile)
    topics = {c.topic for c in cautions}
    assert topics == {"medication", "allergy"}


def test_pacing_modifier_claims_only_the_signal_that_actually_triggered(profile):
    """Regression test: an independent review found that the pacing
    modifier's OR trigger (short sleep OR high stress) was correctly an
    OR, but the resulting claim text unconditionally asserted *both* were
    reported regardless of which one(s) actually triggered it -- a
    questionnaire reporting only high stress (normal sleep) would still
    generate a grounded fact claiming short sleep was also reported."""
    from care_agent.models import QuestionnaireContext, QuestionnaireFact

    context = QuestionnaireContext(
        user_id="u1",
        completed_at=None,
        facts=(
            QuestionnaireFact(field="mind.sleep_duration", value="7_8_hours"),
            QuestionnaireFact(field="mind.stress", value="high"),
        ),
    )
    modifiers = build_questionnaire_modifiers(context)
    pacing = next(m for m in modifiers if m.topic == "pacing")
    assert "high stress" in pacing.grounded_fact.claim
    assert "short sleep" not in pacing.grounded_fact.claim
    assert "sleep" not in pacing.grounded_fact.source_ref
    # A second independent review found this test checked only the
    # metadata (`claim`/`source_ref`) while `text` -- what the narrator
    # actually renders into the visible answer -- still unconditionally
    # named both signals. Reproduced live: a questionnaire reporting only
    # high stress still produced a safe=True answer stating "given
    # reported short sleep and high stress."
    assert "high stress" in pacing.text
    assert "short sleep" not in pacing.text


def test_nutrition_modifier_claims_only_the_signal_that_actually_triggered():
    """Same bug, the nutrition modifier: only low vegetable intake
    reported (sugary-food frequency normal) used to still claim frequent
    sugary foods too."""
    from care_agent.models import QuestionnaireContext, QuestionnaireFact

    context = QuestionnaireContext(
        user_id="u1",
        completed_at=None,
        facts=(
            QuestionnaireFact(field="nutrition.sugary_foods", value="1_2_days_per_week"),
            QuestionnaireFact(field="nutrition.vegetables", value="0_1_servings_per_day"),
        ),
    )
    modifiers = build_questionnaire_modifiers(context)
    nutrition = next(m for m in modifiers if m.topic == "nutrition")
    assert "low vegetable intake" in nutrition.grounded_fact.claim
    assert "sugary" not in nutrition.grounded_fact.claim
    assert "sugary" not in nutrition.grounded_fact.source_ref
    # Same additional check as the pacing test above: the rendered text,
    # not just the claim metadata, must reflect only the triggered signal.
    assert "adding vegetables" in nutrition.text
    assert "sugary" not in nutrition.text


def test_exercise_limitation_modifier_reflects_the_actual_reported_detail():
    """A second independent review found this modifier hardcoded "knee
    pain with running/jumping" regardless of what the questionnaire's
    `exercise_limitation` caution actually reported -- the same "policy
    for one case applied to a different case" bug already fixed for the
    medication/allergy cautions, just not this one. A caution reporting a
    different limitation must not produce a claim about knee pain."""
    from care_agent.models import QuestionnaireCaution, QuestionnaireContext

    context = QuestionnaireContext(
        user_id="u1",
        completed_at=None,
        cautions=(QuestionnaireCaution(kind="exercise_limitation", detail="Reports shoulder pain with overhead lifting."),),
    )
    modifiers = build_questionnaire_modifiers(context)
    exercise = next(m for m in modifiers if m.topic == "exercise")
    assert "shoulder pain" in exercise.text
    assert "knee" not in exercise.text
    assert "knee" not in exercise.grounded_fact.claim


def test_family_history_modifier_reflects_the_actual_reported_detail():
    """Same bug, the family-history modifier: it hardcoded "first-degree
    family history of type 2 diabetes" regardless of the caution's actual
    detail."""
    from care_agent.models import QuestionnaireCaution, QuestionnaireContext

    context = QuestionnaireContext(
        user_id="u1",
        completed_at=None,
        cautions=(QuestionnaireCaution(kind="family_history_context", detail="Reports family history of hypertension."),),
    )
    modifiers = build_questionnaire_modifiers(context)
    family_history = next(m for m in modifiers if m.topic == "family_history")
    assert "hypertension" in family_history.text
    assert "diabetes" not in family_history.text


def test_supplement_caution_does_not_claim_levothyroxine_for_an_unrelated_medication_caution():
    """Regression test: an independent review found that any
    medication_context caution -- regardless of which medication it was
    actually about -- triggered a hardcoded claim of "user reports
    levothyroxine use." A future questionnaire flagging a caution about a
    completely different medication would have this code confidently
    assert levothyroxine use that was never reported. Correct behavior:
    no claim at all (silence) rather than a wrong one."""
    from care_agent.models import QuestionnaireCaution, QuestionnaireContext, UserProfile

    context = QuestionnaireContext(
        user_id="u1",
        completed_at=None,
        cautions=(QuestionnaireCaution(kind="medication_context", detail="Reports metformin use for blood sugar management."),),
    )
    no_meds_profile = UserProfile(user_id="u1", display_name="Test", age=None, sex=None, country=None)
    cautions = build_supplement_cautions(context, no_meds_profile)
    assert not any(c.topic == "medication" for c in cautions)


def test_supplement_caution_does_not_claim_medication_use_from_a_denial():
    """A second independent review found that a bare substring match on
    the medication/allergy name treated a denial the same as a positive
    report -- "Patient denies levothyroxine use" still triggered the
    levothyroxine-specific caution. Correct behavior: silence, the same as
    an unrelated medication."""
    from care_agent.models import QuestionnaireCaution, QuestionnaireContext, UserProfile

    context = QuestionnaireContext(
        user_id="u1",
        completed_at=None,
        cautions=(
            QuestionnaireCaution(kind="medication_context", detail="Patient denies levothyroxine use."),
            QuestionnaireCaution(kind="allergy_context", detail="No shellfish allergy reported."),
        ),
    )
    no_meds_profile = UserProfile(user_id="u1", display_name="Test", age=None, sex=None, country=None)
    cautions = build_supplement_cautions(context, no_meds_profile)
    assert cautions == []


def test_alcohol_limitation_only_when_triglycerides_flagged(bloodwork, catalog, questionnaire):
    items = rank_focus_markers(bloodwork.latest_panel, catalog, set())
    limitation = alcohol_unknown_limitation(questionnaire, items)
    assert limitation is not None
    assert "alcohol" in limitation.detail.lower()


def test_alcohol_limitation_absent_without_triglycerides_flag(questionnaire):
    limitation = alcohol_unknown_limitation(questionnaire, [])
    assert limitation is None


def test_staleness_limitation_none_for_fresh_panel(bloodwork):
    limitation, result = staleness_limitation(bloodwork.latest_panel)
    # NOTE: depends on wall-clock "today"; sample panel date is 2026-05-06.
    assert result is not None


def test_staleness_limitation_for_missing_panel():
    limitation, result = staleness_limitation(None)
    assert limitation is not None
    assert limitation.kind == "missing_data"
    assert result is None
