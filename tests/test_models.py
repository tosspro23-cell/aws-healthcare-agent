"""Tests for `care_agent.models`' pure (de)serialization helpers."""

from __future__ import annotations

from care_agent.models import GroundedFact, grounded_fact_from_dict


def test_grounded_fact_from_dict_round_trips_as_dict_output():
    original = GroundedFact(
        claim="LDL-C trend: 148 mg/dL on 2025-12-08 -> 162 mg/dL on 2026-05-06 (up)",
        source_type="bloodwork",
        source_ref="trend:ldl_c_mg_dl",
        numeric_values=(162.0, 148.0),
        unit="mg/dL",
        display_name="LDL-C",
        display_name_aliases=("ldl c", "ldl cholesterol"),
    )
    rebuilt = grounded_fact_from_dict(original.as_dict())
    assert rebuilt == original


def test_grounded_fact_from_dict_handles_minimal_dict_with_defaults_missing():
    """A fact with no unit/display_name (e.g. a qualitative relationship
    claim like `compare_marker_trends`'s own) still round-trips -- the
    optional fields must not be required keys."""
    d = {"claim": "X and Y are both trending up.", "source_type": "bloodwork", "source_ref": "trend_comparison:x:y"}
    rebuilt = grounded_fact_from_dict(d)
    assert rebuilt == GroundedFact(claim=d["claim"], source_type="bloodwork", source_ref=d["source_ref"])
