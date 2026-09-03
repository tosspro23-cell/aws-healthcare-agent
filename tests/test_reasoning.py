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
