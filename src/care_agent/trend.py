"""Deterministic trend computation, gated by the lipid/trend policy in the KB.

Policy (``kb_lipid_008``): only describe a trend when at least two dated
measurements for the *same concept and unit* are available. Otherwise report
the latest value and explicitly say a trend cannot be determined -- never
infer direction from a single point.
"""

from __future__ import annotations

from dataclasses import dataclass

from care_agent.models import Bloodwork


@dataclass(frozen=True)
class TrendResult:
    concept_id: str
    available: bool
    direction: str | None = None  # "up" | "down" | "flat" | None
    latest_value: float | None = None
    latest_date: str | None = None
    previous_value: float | None = None
    previous_date: str | None = None
    unit: str | None = None
    reason_unavailable: str | None = None


def compute_trend(bloodwork: Bloodwork, concept_id: str) -> TrendResult:
    panels = bloodwork.all_panels_newest_first()
    points: list[tuple[str, float, str]] = []  # (date, value, unit)
    for panel in panels:
        marker = panel.get(concept_id)
        if marker is not None:
            points.append((panel.measurement_date, marker.value, marker.unit))

    if not points:
        return TrendResult(
            concept_id=concept_id,
            available=False,
            reason_unavailable="No measurements for this marker were found in any panel.",
        )

    if len(points) == 1:
        date, value, unit = points[0]
        return TrendResult(
            concept_id=concept_id,
            available=False,
            latest_value=value,
            latest_date=date,
            unit=unit,
            reason_unavailable=(
                "Only one dated measurement is available for this marker, so a trend "
                "direction cannot be determined (per lipid-trend policy: at least two "
                "same-unit measurements are required)."
            ),
        )

    (latest_date, latest_value, latest_unit), (prev_date, prev_value, prev_unit) = points[0], points[1]
    if latest_unit != prev_unit:
        return TrendResult(
            concept_id=concept_id,
            available=False,
            latest_value=latest_value,
            latest_date=latest_date,
            unit=latest_unit,
            reason_unavailable=(
                f"Measurements use different units ({prev_unit!r} vs {latest_unit!r}); "
                "a trend cannot be safely compared without a validated conversion."
            ),
        )

    if latest_value > prev_value:
        direction = "up"
    elif latest_value < prev_value:
        direction = "down"
    else:
        direction = "flat"

    return TrendResult(
        concept_id=concept_id,
        available=True,
        direction=direction,
        latest_value=latest_value,
        latest_date=latest_date,
        previous_value=prev_value,
        previous_date=prev_date,
        unit=latest_unit,
    )
