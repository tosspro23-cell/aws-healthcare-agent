from care_agent.models import GroundedFact
from care_agent.safety import (
    check_no_diagnosis,
    check_no_dosing,
    check_non_empty,
    run_safety_checks,
    verify_numeric_grounding,
)


def test_check_no_diagnosis_catches_direct_claim():
    check = check_no_diagnosis("Based on this, you have type 2 diabetes.")
    assert check.passed is False


def test_check_no_diagnosis_allows_risk_framing():
    check = check_no_diagnosis("This value sits in a range commonly associated with higher risk.")
    assert check.passed is True


def test_check_no_diagnosis_catches_condition_is_your_condition_phrasing():
    """Regression test: an independent review found this exact phrasing
    escaped the original pattern list (which only matched "you have X"
    style phrasings, not "X is your condition")."""
    check = check_no_diagnosis("Diabetes is your confirmed condition.")
    assert check.passed is False


def test_check_no_dosing_catches_explicit_dose():
    check = check_no_dosing("Take 500 mg daily of this supplement.")
    assert check.passed is False


def test_check_no_dosing_catches_start_stop_language():
    check = check_no_dosing("You should start taking a vitamin D supplement right away.")
    assert check.passed is False


def test_check_no_dosing_allows_generic_education():
    check = check_no_dosing("A clinician or pharmacist can advise on whether a supplement is appropriate.")
    assert check.passed is True


def test_check_no_dosing_catches_word_form_dosing_instruction():
    """Regression test: an independent review found that written-word
    dosing/frequency instructions with no digits at all (so none of the
    numeric dosing patterns apply) escaped detection entirely."""
    check = check_no_dosing("Swallow one vitamin D capsule every morning.")
    assert check.passed is False


def test_check_non_empty_flags_empty_string():
    """Regression test: an independent review found that an empty answer
    passes every other check trivially (no diagnosis pattern matches
    nothing, no ungrounded number in nothing) without a check that
    actually verifies there's an answer at all."""
    assert check_non_empty("").passed is False
    assert check_non_empty("   ").passed is False
    assert check_non_empty("Your LDL-C is 162 mg/dL.").passed is True


def test_numeric_grounding_passes_for_grounded_number():
    facts = [GroundedFact(claim="ldl", source_type="bloodwork", source_ref="p1:ldl", numeric_values=(162.0,), unit="mg/dL")]
    check = verify_numeric_grounding("Your LDL-C is 162 mg/dL.", facts)
    assert check.passed is True


def test_numeric_grounding_fails_for_invented_number():
    facts = [GroundedFact(claim="ldl", source_type="bloodwork", source_ref="p1:ldl", numeric_values=(162.0,), unit="mg/dL")]
    check = verify_numeric_grounding("Your LDL-C is 999 mg/dL.", facts)
    assert check.passed is False
    assert "999" in check.detail


def test_numeric_grounding_fails_for_number_glued_to_unit_with_no_space():
    """Regression test: an independent review found that `_NUMBER_RE`'s
    trailing negative lookahead blocked matching a number immediately
    followed by a letter, so "999mg" (no space before the unit) escaped
    numeric extraction entirely -- any fabricated value could bypass the
    check just by omitting a space before its unit."""
    facts = [GroundedFact(claim="ldl", source_type="bloodwork", source_ref="p1:ldl", numeric_values=(162.0,), unit="mg/dL")]
    check = verify_numeric_grounding("Your LDL-C is 999mg/dL.", facts)
    assert check.passed is False


def test_numeric_grounding_fails_when_a_real_value_is_attached_to_the_wrong_marker():
    """Regression test: an independent review found that grounding only
    checked "does this number appear somewhere in the grounded facts,"
    with no check on *which* marker/unit it's attached to -- so a real,
    correctly-grounded LDL-C value (162 mg/dL) could be reattached to a
    completely different marker (HbA1c, unit %) and still pass, because
    162 is a real grounded number. The fix checks (value, unit) pairs
    together for any number with a recognized unit immediately adjacent."""
    facts = [
        GroundedFact(claim="ldl", source_type="bloodwork", source_ref="p1:ldl", numeric_values=(162.0,), unit="mg/dL"),
        GroundedFact(claim="a1c", source_type="bloodwork", source_ref="p1:a1c", numeric_values=(6.1,), unit="%"),
    ]
    check = verify_numeric_grounding("Your HbA1c is 162%.", facts)
    assert check.passed is False


def test_numeric_grounding_allows_ordinal_list_markers_only_at_the_list_position():
    facts: list[GroundedFact] = []
    check = verify_numeric_grounding("1. First item\n2. Second item", facts)
    assert check.passed is True


def test_numeric_grounding_still_flags_a_number_matching_an_ordinal_value_used_elsewhere():
    """Regression test: an independent review found that the old
    `allowed_extra_numbers` mechanism exempted ordinal values (1-5)
    *anywhere* in the text, not just at the list-marker position where
    they're actually safe -- so "Your LDL-C is 5 mg/dL" passed just
    because 5 happens to be a valid list-numbering value elsewhere. The
    fix exempts only the exact character span of a line-leading "N. "
    marker, not the numeral's value globally."""
    facts = [GroundedFact(claim="ldl", source_type="bloodwork", source_ref="p1:ldl", numeric_values=(162.0,), unit="mg/dL")]
    check = verify_numeric_grounding("1. Focus on your results.\n2. Your LDL-C is 5 mg/dL.", facts)
    assert check.passed is False
    assert "5mg/dL" in check.detail or "5" in check.detail


def test_numeric_grounding_ignores_dates_when_allowed():
    facts: list[GroundedFact] = []
    check = verify_numeric_grounding("Measured on 2026-05-06.", facts, allowed_dates={"2026-05-06"})
    assert check.passed is True


def test_numeric_grounding_flags_ungrounded_date():
    facts: list[GroundedFact] = []
    check = verify_numeric_grounding("Measured on 2099-01-01.", facts, allowed_dates={"2026-05-06"})
    assert check.passed is False
    assert "2099-01-01" in check.detail


def test_run_safety_checks_aggregates_all_four():
    facts = [GroundedFact(claim="ldl", source_type="bloodwork", source_ref="p1:ldl", numeric_values=(162.0,), unit="mg/dL")]
    report = run_safety_checks("You have diabetes with LDL 162 mg/dL, take 50 mg daily.", facts)
    assert report.passed is False
    names = {c.name for c in report.failed_checks}
    assert "no_diagnosis" in names
    assert "no_dosing" in names


def test_run_safety_checks_flags_empty_answer():
    report = run_safety_checks("", [])
    assert report.passed is False
    names = {c.name for c in report.failed_checks}
    assert "non_empty" in names
