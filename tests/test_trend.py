from care_agent.models import Biomarker, Bloodwork, Panel
from care_agent.trend import compute_trend


def _biomarker(concept_id, value, unit="mg/dL"):
    return Biomarker(concept_id=concept_id, display_name=concept_id, value=value, unit=unit)


def test_trend_up_with_two_panels():
    bw = Bloodwork(
        user_id="u1",
        latest_panel=Panel(panel_id="p2", measurement_date="2026-05-06", biomarkers=(_biomarker("ldl_c_mg_dl", 162),)),
        previous_panels=(Panel(panel_id="p1", measurement_date="2025-12-08", biomarkers=(_biomarker("ldl_c_mg_dl", 148),)),),
    )
    result = compute_trend(bw, "ldl_c_mg_dl")
    assert result.available is True
    assert result.direction == "up"
    assert result.latest_value == 162
    assert result.previous_value == 148


def test_trend_down():
    bw = Bloodwork(
        user_id="u1",
        latest_panel=Panel(panel_id="p2", measurement_date="2026-05-06", biomarkers=(_biomarker("ldl_c_mg_dl", 130),)),
        previous_panels=(Panel(panel_id="p1", measurement_date="2025-12-08", biomarkers=(_biomarker("ldl_c_mg_dl", 148),)),),
    )
    result = compute_trend(bw, "ldl_c_mg_dl")
    assert result.direction == "down"


def test_trend_flat():
    bw = Bloodwork(
        user_id="u1",
        latest_panel=Panel(panel_id="p2", measurement_date="2026-05-06", biomarkers=(_biomarker("ldl_c_mg_dl", 148),)),
        previous_panels=(Panel(panel_id="p1", measurement_date="2025-12-08", biomarkers=(_biomarker("ldl_c_mg_dl", 148),)),),
    )
    result = compute_trend(bw, "ldl_c_mg_dl")
    assert result.direction == "flat"


def test_trend_unavailable_single_measurement():
    bw = Bloodwork(
        user_id="u1",
        latest_panel=Panel(panel_id="p2", measurement_date="2026-05-06", biomarkers=(_biomarker("fasting_glucose_mg_dl", 108),)),
        previous_panels=(Panel(panel_id="p1", measurement_date="2025-12-08", biomarkers=(_biomarker("ldl_c_mg_dl", 148),)),),
    )
    result = compute_trend(bw, "fasting_glucose_mg_dl")
    assert result.available is False
    assert result.latest_value == 108
    assert "one dated measurement" in result.reason_unavailable


def test_trend_unavailable_no_data_at_all():
    bw = Bloodwork(user_id="u1", latest_panel=None, previous_panels=())
    result = compute_trend(bw, "ldl_c_mg_dl")
    assert result.available is False
    assert result.latest_value is None
    assert "No measurements" in result.reason_unavailable


def test_trend_unavailable_unit_mismatch():
    bw = Bloodwork(
        user_id="u1",
        latest_panel=Panel(panel_id="p2", measurement_date="2026-05-06", biomarkers=(_biomarker("ldl_c_mg_dl", 4.2, unit="mmol/L"),)),
        previous_panels=(Panel(panel_id="p1", measurement_date="2025-12-08", biomarkers=(_biomarker("ldl_c_mg_dl", 148, unit="mg/dL"),)),),
    )
    result = compute_trend(bw, "ldl_c_mg_dl")
    assert result.available is False
    assert "different units" in result.reason_unavailable


def test_trend_uses_most_recent_two_when_three_panels_exist():
    bw = Bloodwork(
        user_id="u1",
        latest_panel=Panel(panel_id="p3", measurement_date="2026-05-06", biomarkers=(_biomarker("ldl_c_mg_dl", 162),)),
        previous_panels=(
            Panel(panel_id="p2", measurement_date="2025-12-08", biomarkers=(_biomarker("ldl_c_mg_dl", 148),)),
            Panel(panel_id="p1", measurement_date="2024-06-01", biomarkers=(_biomarker("ldl_c_mg_dl", 200),)),
        ),
    )
    result = compute_trend(bw, "ldl_c_mg_dl")
    assert result.previous_value == 148
    assert result.previous_date == "2025-12-08"
