import pytest

from care_agent.data_store import DataStore, UnknownUserError


def test_get_user_profile(data_dir):
    store = DataStore(data_dir)
    profile = store.get_user_profile("user_demo_001")
    assert profile.display_name == "Alex"
    assert profile.age == 42
    assert any(m.name == "levothyroxine" for m in profile.medications)
    assert any(a.name == "shellfish" for a in profile.allergies)


def test_get_bloodwork(data_dir):
    store = DataStore(data_dir)
    bw = store.get_bloodwork("user_demo_001")
    assert bw.latest_panel is not None
    assert bw.latest_panel.panel_id == "panel_2026_05_06_demo"
    ldl = bw.latest_panel.get("ldl_c_mg_dl")
    assert ldl is not None
    assert ldl.value == 162
    assert len(bw.previous_panels) == 1


def test_get_questionnaire_context(data_dir):
    store = DataStore(data_dir)
    ctx = store.get_questionnaire_context("user_demo_001")
    assert ctx.has_caution_kind("exercise_limitation")
    assert ctx.fact("nutrition.sugary_foods").value == "3_4_days_per_week"
    assert any(d.field == "mental_health.phq2" for d in ctx.declined)
    assert any(u.field == "nutrition.alcohol_intake" for u in ctx.unknowns)


def test_wrong_user_raises(data_dir):
    store = DataStore(data_dir)
    with pytest.raises(UnknownUserError):
        store.get_user_profile("someone_else")
    with pytest.raises(UnknownUserError):
        store.get_bloodwork("someone_else")
    with pytest.raises(UnknownUserError):
        store.get_questionnaire_context("someone_else")


def test_sample_questions_loaded(data_dir):
    store = DataStore(data_dir)
    questions = store.get_sample_questions()
    ids = {q["id"] for q in questions}
    assert {"q_main", "q_missing_context", "q_supplements"}.issubset(ids)
