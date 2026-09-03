from datetime import date

from care_agent.staleness import POTENTIALLY_STALE_DAYS, STALE_DAYS, assess_staleness


def test_fresh_panel():
    result = assess_staleness("2026-05-06", as_of=date(2026, 6, 1))
    assert result.level == "fresh"


def test_potentially_stale_boundary_just_over():
    as_of = date(2026, 5, 6)
    measured = date(2025, 10, 1)  # > 183 days, < 365 days before as_of
    result = assess_staleness(measured.isoformat(), as_of=as_of)
    assert result.level == "potentially_stale"


def test_stale_boundary_just_over():
    as_of = date(2027, 5, 6)
    measured = date(2026, 1, 1)  # > 365 days before as_of
    result = assess_staleness(measured.isoformat(), as_of=as_of)
    assert result.level == "stale"


def test_exactly_at_potentially_stale_threshold_is_still_fresh():
    as_of = date(2026, 1, 1)
    measured_date = date.fromordinal(as_of.toordinal() - POTENTIALLY_STALE_DAYS)
    result = assess_staleness(measured_date.isoformat(), as_of=as_of)
    assert result.level == "fresh"


def test_exactly_at_stale_threshold_is_potentially_stale_not_stale():
    as_of = date(2026, 1, 1)
    measured_date = date.fromordinal(as_of.toordinal() - STALE_DAYS)
    result = assess_staleness(measured_date.isoformat(), as_of=as_of)
    assert result.level == "potentially_stale"


def test_future_measurement_date_is_fresh_not_negative_age():
    # Defensive: a clock-skew / bad-data future date shouldn't crash or
    # produce a nonsensical "stale" classification.
    result = assess_staleness("2099-01-01", as_of=date(2026, 1, 1))
    assert result.age_days < 0
    assert result.level == "fresh"
