"""Recency / staleness policy for bloodwork panels.

Policy (``kb_stale_data_002``, reference mock policy -- not clinical
guidance): a panel older than 6 months is *potentially stale* for
priority-setting, and a panel older than 12 months is *clearly stale* and
should be flagged. All thresholds are configurable so tests can pin a
reference "now" instead of depending on wall-clock time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

POTENTIALLY_STALE_DAYS = 183  # ~6 months
STALE_DAYS = 365  # ~12 months


@dataclass(frozen=True)
class StalenessResult:
    measurement_date: str
    age_days: int
    level: str  # "fresh" | "potentially_stale" | "stale"

    @property
    def is_stale_or_potentially_stale(self) -> bool:
        return self.level != "fresh"


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(value).date()


def assess_staleness(measurement_date: str, as_of: date | None = None) -> StalenessResult:
    as_of = as_of or date.today()
    measured = _parse_date(measurement_date)
    age_days = (as_of - measured).days
    if age_days > STALE_DAYS:
        level = "stale"
    elif age_days > POTENTIALLY_STALE_DAYS:
        level = "potentially_stale"
    else:
        level = "fresh"
    return StalenessResult(measurement_date=measurement_date, age_days=age_days, level=level)
