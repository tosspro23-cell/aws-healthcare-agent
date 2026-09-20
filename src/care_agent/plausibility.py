"""Physiological plausibility bounds for raw biomarker values.

**What this is not**: this is not a clinical "normal"/"high"/"low"
range -- that classification already comes from the source dataset
(``Biomarker.classification``) and this project deliberately never
recomputes it (``kb_grounding_003``; see ``catalog.py``'s own docstring).
This module checks something upstream and different: whether the raw
*number itself* is a value a living human could actually have, at all --
not whether it's concerning.

**Why this exists**: every safety check in ``safety.py`` verifies that a
narrated answer's numbers trace back to a ``GroundedFact`` -- but a
``GroundedFact`` built from a data-entry error (a decimal point dropped,
a unit confused, a transcription typo) is still, mechanically, "grounded."
A number can be perfectly grounded in the source data and still be wrong,
because the source data itself is wrong -- this gap is real, not
hypothetical (see the architecture discussion in ``docs/DECISIONS.md``,
2026-09-19 entry, and the market research cited there on enterprise
"claim verification chain" patterns: a grounded claim can still trace to
a bad source). This module is the first, narrow layer that questions the
source value itself, independent of what it's later used for.

**Deliberately not a KB policy rule**: unlike the `kb_*`-numbered checks
in ``docs/ARCHITECTURE.md``'s policy table, these bounds don't trace back
to a `Nuaura mock policy` entry in ``knowledge_base.jsonl`` -- they're
this project's own addition, sourced from general, widely-documented
clinical chemistry reference ranges for the outer bound of what's ever
been reported in a living patient (including severe/critical cases), not
independently verified against a specific medical reference for this
change. **A real deployment handling real patient data should have a
clinician review and own these bounds**, the same "non-clinical,
synthetic reference implementation" caveat this project states
throughout (see README.md) -- this is a reference implementation of the
*pattern*, not a clinically-validated bounds table.

Bounds are deliberately wide: the goal is to catch a value nothing
physiologically supports (a negative concentration, a value 10x outside
any documented case, an obvious unit mix-up), never to flag a merely
severe-but-real abnormal result. A value inside these bounds says
nothing about whether it's healthy -- only that it's not obviously
impossible.
"""

from __future__ import annotations

from dataclasses import dataclass

from care_agent.models import Biomarker

# (min, max), inclusive, in the unit the catalog already reports that
# marker in (see `data/mock_biomarker_catalog.sqlite`). Sourced from
# documented outer bounds for severe/critical real-world cases (e.g.
# homozygous familial hypercholesterolemia for LDL-C, DKA/HHS for
# glucose, acute liver failure for ALT/AST) -- not a "normal" range.
_PLAUSIBLE_BOUNDS: dict[str, tuple[float, float]] = {
    "ldl_c_mg_dl": (0.0, 1000.0),
    "hdl_c_mg_dl": (0.0, 200.0),
    "triglycerides_mg_dl": (0.0, 10000.0),
    "total_cholesterol_mg_dl": (0.0, 1500.0),
    "non_hdl_c_mg_dl": (0.0, 1400.0),
    "apob_mg_dl": (0.0, 300.0),
    "lp_a_mg_dl": (0.0, 500.0),
    "hba1c_percent": (3.0, 20.0),
    "fasting_glucose_mg_dl": (20.0, 1000.0),
    "fasting_insulin_uiu_ml": (0.0, 1000.0),
    "hs_crp_mg_l": (0.0, 500.0),
    "ferritin_ng_ml": (0.0, 100000.0),
    "vitamin_d_25oh_ng_ml": (0.0, 500.0),
    "vitamin_b12_pg_ml": (0.0, 20000.0),
    "tsh_miu_l": (0.0, 500.0),
    "free_t4_ng_dl": (0.0, 10.0),
    "cortisol_morning_ug_dl": (0.0, 200.0),
    "egfr_ml_min_1_73m2": (0.0, 200.0),
    "creatinine_mg_dl": (0.0, 30.0),
    "alt_u_l": (0.0, 10000.0),
    "ast_u_l": (0.0, 10000.0),
    "ggt_u_l": (0.0, 5000.0),
    "hemoglobin_g_dl": (0.0, 25.0),
    "wbc_10e3_ul": (0.0, 500.0),
}


# Public: the set of concept_ids this project has real coverage for --
# reused by `orchestrator.py`'s capability gate so a tool-calling plan can
# only reference a marker this system actually knows how to look up,
# instead of duplicating a second list that could drift from this one.
SUPPORTED_CONCEPT_IDS: tuple[str, ...] = tuple(_PLAUSIBLE_BOUNDS)


@dataclass(frozen=True)
class PlausibilityResult:
    concept_id: str
    value: float
    unit: str
    is_plausible: bool
    bounds: tuple[float, float] | None  # None when this marker has no bounds entry (unrecognized concept_id)


def assess_plausibility(marker: Biomarker) -> PlausibilityResult:
    """Check whether `marker.value` falls within this project's outer
    physiological plausibility bounds. A marker with no entry in
    `_PLAUSIBLE_BOUNDS` (a concept_id this table doesn't yet cover) is
    treated as plausible -- silently passing an unrecognized marker
    rather than flagging it is deliberate: this check should never be
    the reason a new, legitimate marker type looks broken."""
    bounds = _PLAUSIBLE_BOUNDS.get(marker.concept_id)
    if bounds is None:
        return PlausibilityResult(concept_id=marker.concept_id, value=marker.value, unit=marker.unit, is_plausible=True, bounds=None)
    low, high = bounds
    return PlausibilityResult(
        concept_id=marker.concept_id,
        value=marker.value,
        unit=marker.unit,
        is_plausible=low <= marker.value <= high,
        bounds=bounds,
    )
