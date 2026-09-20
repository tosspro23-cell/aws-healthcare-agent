from __future__ import annotations

from care_agent.models import Biomarker
from care_agent.plausibility import assess_plausibility


def _marker(concept_id: str, value: float, unit: str) -> Biomarker:
    return Biomarker(concept_id=concept_id, display_name=concept_id, value=value, unit=unit, classification="normal")


def test_value_inside_bounds_is_plausible():
    result = assess_plausibility(_marker("ldl_c_mg_dl", 162.0, "mg/dL"))
    assert result.is_plausible is True


def test_severe_but_real_value_is_still_plausible():
    """Bounds are outer physiological limits, not a "normal" clinical range --
    a severe, real, documented case (e.g. triglycerides in acute
    pancreatitis-risk territory) must not be flagged just for being
    abnormal."""
    result = assess_plausibility(_marker("triglycerides_mg_dl", 3500.0, "mg/dL"))
    assert result.is_plausible is True


def test_value_far_outside_bounds_is_implausible():
    result = assess_plausibility(_marker("ldl_c_mg_dl", 50000.0, "mg/dL"))
    assert result.is_plausible is False
    assert result.bounds == (0.0, 1000.0)


def test_negative_value_is_implausible():
    result = assess_plausibility(_marker("hba1c_percent", -5.0, "%"))
    assert result.is_plausible is False


def test_unrecognized_marker_is_not_flagged():
    """A concept_id with no entry in the bounds table passes silently --
    this check should never be the reason a new, legitimate marker type
    looks broken."""
    result = assess_plausibility(_marker("some_new_marker_id", 999999.0, "widgets"))
    assert result.is_plausible is True
    assert result.bounds is None
