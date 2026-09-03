from care_agent.models import GroundedFact
from care_agent.safety import (
    check_no_diagnosis,
    check_no_dosing,
    run_safety_checks,
    verify_numeric_grounding,
)


def test_check_no_diagnosis_catches_direct_claim():
    check = check_no_diagnosis("Based on this, you have type 2 diabetes.")
    assert check.passed is False


def test_check_no_diagnosis_allows_risk_framing():
    check = check_no_diagnosis("This value sits in a range commonly associated with higher risk.")
    assert check.passed is True


def test_check_no_dosing_catches_explicit_dose():
    check = check_no_dosing("Take 500 mg daily of this supplement.")
    assert check.passed is False


def test_check_no_dosing_catches_start_stop_language():
    check = check_no_dosing("You should start taking a vitamin D supplement right away.")
    assert check.passed is False


def test_check_no_dosing_allows_generic_education():
    check = check_no_dosing("A clinician or pharmacist can advise on whether a supplement is appropriate.")
    assert check.passed is True


def test_numeric_grounding_passes_for_grounded_number():
    facts = [GroundedFact(claim="ldl", source_type="bloodwork", source_ref="p1:ldl", numeric_values=(162.0,))]
    check = verify_numeric_grounding("Your LDL-C is 162 mg/dL.", facts)
    assert check.passed is True


def test_numeric_grounding_fails_for_invented_number():
    facts = [GroundedFact(claim="ldl", source_type="bloodwork", source_ref="p1:ldl", numeric_values=(162.0,))]
    check = verify_numeric_grounding("Your LDL-C is 999 mg/dL.", facts)
    assert check.passed is False
    assert "999" in check.detail


def test_numeric_grounding_ignores_dates_when_allowed():
    facts: list[GroundedFact] = []
    check = verify_numeric_grounding("Measured on 2026-05-06.", facts, allowed_dates={"2026-05-06"})
    assert check.passed is True


def test_numeric_grounding_flags_ungrounded_date():
    facts: list[GroundedFact] = []
    check = verify_numeric_grounding("Measured on 2099-01-01.", facts, allowed_dates={"2026-05-06"})
    assert check.passed is False
    assert "2099-01-01" in check.detail


def test_numeric_grounding_allows_ordinal_markers():
    facts: list[GroundedFact] = []
    check = verify_numeric_grounding("1. First item\n2. Second item", facts, allowed_extra_numbers={1.0, 2.0})
    assert check.passed is True


def test_run_safety_checks_aggregates_all_three():
    facts = [GroundedFact(claim="ldl", source_type="bloodwork", source_ref="p1:ldl", numeric_values=(162.0,))]
    report = run_safety_checks("You have diabetes with LDL 162 mg/dL, take 50 mg daily.", facts)
    assert report.passed is False
    names = {c.name for c in report.failed_checks}
    assert "no_diagnosis" in names
    assert "no_dosing" in names
