"""The deterministic reasoning core: turns raw data + retrieval into a ``Brief``.

This module is the "brain" of the agent. It never produces user-facing prose
-- it produces a structured, fully-grounded ``Brief`` (ranked concerns,
questionnaire modifiers, limitations, and citations) that a narrator
(``narrator/mock_narrator.py`` or an optional LLM narrator) turns into text.
Keeping reasoning and narration separate means:

* the reasoning logic is unit-testable without touching any prose,
* an LLM narrator can only rephrase what is already grounded here -- it has
  no path to invent a fact, because the safety layer re-verifies every
  number in the final text against ``Brief.grounded_facts``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from care_agent.catalog import BiomarkerCatalog
from care_agent.models import (
    Biomarker,
    CatalogEntry,
    GroundedFact,
    Limitation,
    Panel,
    QuestionnaireContext,
    RetrievedChunk,
    UserProfile,
)
from care_agent.staleness import StalenessResult, assess_staleness
from care_agent.trend import TrendResult

_SEVERITY_WEIGHTS = {
    "high": 3.0,
    "elevated": 3.0,
    "borderline_high": 2.0,
    "suboptimal": 2.0,
    "low": 2.0,
    "borderline": 1.5,
    "adequate": 0.0,
    "optimal": 0.0,
}

_IMPORTANCE_WEIGHTS = {"high": 1.5, "medium": 1.0, "low": 0.5}

# Markers that, when jointly elevated/borderline, form the "metabolic
# priority pattern" called out by kb_a1c_005.
_METABOLIC_PATTERN_CONCEPTS = {"hba1c_percent", "fasting_glucose_mg_dl", "triglycerides_mg_dl"}

# Maps a biomarker concept_id to the knowledge_base.jsonl topic tags most
# relevant to it, used to boost retrieval toward marker-specific policy and
# education chunks instead of relying on lexical overlap alone.
CONCEPT_TOPIC_TAGS: dict[str, set[str]] = {
    "ldl_c_mg_dl": {"ldl", "cholesterol", "cardiovascular"},
    "hdl_c_mg_dl": {"hdl", "cholesterol"},
    "triglycerides_mg_dl": {"triglycerides", "lipids", "metabolic"},
    "total_cholesterol_mg_dl": {"cholesterol"},
    "hba1c_percent": {"hba1c", "glucose", "metabolic"},
    "fasting_glucose_mg_dl": {"fasting_glucose", "glucose", "metabolic"},
    "hs_crp_mg_l": {"crp", "hs_crp", "inflammation"},
    "vitamin_d_25oh_ng_ml": {"vitamin_d", "25oh_vitamin_d", "supplements"},
    "tsh_miu_l": {"tsh", "thyroid", "levothyroxine"},
    "alt_u_l": {"alt", "liver"},
    "egfr_ml_min_1_73m2": {"egfr", "kidney"},
}

INTENT_TOPIC_TAGS: dict[str, set[str]] = {
    "priority_focus": {"grounding", "safety", "questionnaire", "answering"},
    "trend_check": {"trend", "grounding", "testing"},
    "supplement_safety": {"supplements", "safety", "medications"},
    "general_bloodwork_question": {"grounding"},
}


def severity_weight(classification: str | None) -> float:
    if not classification:
        return 1.0  # unknown classification -> treat cautiously, don't ignore
    return _SEVERITY_WEIGHTS.get(classification.lower(), 1.0)


def importance_weight(catalog_entry: CatalogEntry | None) -> float:
    if catalog_entry is None or not catalog_entry.importance:
        return 1.0
    return _IMPORTANCE_WEIGHTS.get(catalog_entry.importance.lower(), 1.0)


_DETAIL_NUMBER_RE = re.compile(r"\d+\.?\d*")
_LEADING_REPORTS_RE = re.compile(r"^\s*reports?\s+", re.IGNORECASE)


def _naturalize_detail(detail: str) -> str:
    """Turn a questionnaire caution's stored `detail` sentence (e.g.
    "Reports knee pain with running or jumping.") into a lowercase noun
    phrase ("knee pain with running or jumping") that reads naturally when
    embedded mid-sentence in a composed answer, instead of literally
    quoting a capitalized, period-terminated fragment inline -- which is
    grammatically awkward and reads as mechanical/templated (raised
    directly against a live Workbench answer)."""
    text = _LEADING_REPORTS_RE.sub("", detail).strip().rstrip(".")
    return text[:1].lower() + text[1:] if text else text


def _numbers_in(text: str) -> tuple[float, ...]:
    """Extract any numbers literally present in a questionnaire caution's
    own ``detail`` text (e.g. the "2" in "type 2 diabetes"), so a
    ``GroundedFact`` that quotes that detail verbatim in its rendered
    ``text`` doesn't get flagged as containing an *ungrounded* number by
    ``safety.verify_numeric_grounding``. This number came from the actual
    reported data, not a narrator inventing it -- it just needs to be
    registered as grounded like any other sourced value.
    """
    return tuple(float(m) for m in _DETAIL_NUMBER_RE.findall(text))


@dataclass(frozen=True)
class FocusItem:
    marker: Biomarker
    catalog_entry: CatalogEntry | None
    rank_score: float
    mentioned_by_user: bool


def rank_focus_markers(panel: Panel, catalog: BiomarkerCatalog, mentioned_concepts: set[str]) -> list[FocusItem]:
    items: list[FocusItem] = []
    for marker in panel.biomarkers:
        catalog_entry = catalog.lookup(marker.concept_id)
        sev = severity_weight(marker.classification)
        if sev <= 0:
            continue
        imp = importance_weight(catalog_entry)
        mentioned = marker.concept_id in mentioned_concepts
        score = sev * imp + (0.5 if mentioned else 0.0)
        items.append(FocusItem(marker=marker, catalog_entry=catalog_entry, rank_score=score, mentioned_by_user=mentioned))
    items.sort(key=lambda it: it.rank_score, reverse=True)
    return items


def detect_metabolic_priority_pattern(focus_items: list[FocusItem]) -> bool:
    present = {it.marker.concept_id for it in focus_items}
    return _METABOLIC_PATTERN_CONCEPTS.issubset(present)


@dataclass
class QuestionnaireModifier:
    """One piece of questionnaire-driven personalization the answer should apply."""

    topic: str  # e.g. "exercise", "nutrition", "pacing", "family_history"
    text: str
    grounded_fact: GroundedFact


def build_questionnaire_modifiers(context: QuestionnaireContext) -> list[QuestionnaireModifier]:
    modifiers: list[QuestionnaireModifier] = []

    exercise_caution = context.caution("exercise_limitation")
    if exercise_caution is not None:
        pref = next((p for p in context.preferences if p.field == "exercise.preference"), None)
        pref_text = f" ({pref.value.replace('_', ' ')} preferred)" if pref else ""
        # Render the caution's own reported detail rather than a hardcoded
        # "knee pain with running/jumping" -- an independent review caught
        # that this modifier asserted a specific limitation regardless of
        # what the questionnaire actually reported (the same "policy for
        # one case applied to a different case" failure mode as the
        # medication/allergy cautions below).
        modifiers.append(
            QuestionnaireModifier(
                topic="exercise",
                text=f"prefer low-impact activity{pref_text}, given your reported {_naturalize_detail(exercise_caution.detail)}",
                grounded_fact=GroundedFact(
                    claim=f"questionnaire reports exercise limitation: {exercise_caution.detail}",
                    source_type="questionnaire",
                    source_ref="cautions.exercise_limitation",
                    numeric_values=_numbers_in(exercise_caution.detail),
                ),
            )
        )

    sugary = context.fact("nutrition.sugary_foods")
    veg = context.fact("nutrition.vegetables")
    sugary_triggered = bool(sugary and sugary.value in {"3_4_days_per_week", "5_6_days_per_week", "daily"})
    veg_triggered = bool(veg and veg.value in {"0_1_servings_per_day"})
    if sugary_triggered or veg_triggered:
        # The trigger is OR (either signal alone is enough to raise this
        # modifier), but the claim text used to unconditionally assert
        # *both* were reported regardless of which one(s) actually
        # triggered -- an independent review caught this. Build the claim
        # and its source_ref from only the signal(s) that actually fired.
        reported_parts = []
        source_refs = []
        if sugary_triggered:
            reported_parts.append("frequent sugary foods")
            source_refs.append("facts.nutrition.sugary_foods")
        if veg_triggered:
            reported_parts.append("low vegetable intake")
            source_refs.append("facts.nutrition.vegetables")
        pref = next((p for p in context.preferences if p.field == "nutrition.preference"), None)
        pref_text = f", leaning on {pref.value.replace('_', ' ')}" if pref else ""
        # The visible modifier text, like the claim above it, must reflect
        # only the signal(s) that actually triggered -- an independent
        # review caught that round 2's fix corrected `claim`/`source_ref`
        # but left this `text` (what the narrator actually renders into the
        # answer) unconditionally naming both signals.
        nutrition_action_parts = []
        if sugary_triggered:
            nutrition_action_parts.append("reducing sugary foods")
        if veg_triggered:
            nutrition_action_parts.append("adding vegetables")
        modifiers.append(
            QuestionnaireModifier(
                topic="nutrition",
                text=f"prioritize {' and '.join(nutrition_action_parts)}{pref_text}",
                grounded_fact=GroundedFact(
                    claim=f"questionnaire reports {' and '.join(reported_parts)}",
                    source_type="questionnaire",
                    source_ref=",".join(source_refs),
                ),
            )
        )

    aerobic = context.fact("exercise.aerobic_activity")
    if aerobic and aerobic.value == "less_than_60_min_per_week":
        # numeric_values registers the "60" in the claim below as grounded
        # -- found needed live while reusing this exact claim text in
        # mock_narrator.py's closing summary: without it, that summary
        # sentence embeds a number (from this fixed policy threshold, not
        # invented) that verify_numeric_grounding correctly doesn't yet
        # know is sourced, and rejects the whole answer as ungrounded.
        # Same pattern already used for exercise_limitation/
        # family_history_context's caution-detail text below.
        claim = "questionnaire reports less than 60 minutes of aerobic activity per week"
        modifiers.append(
            QuestionnaireModifier(
                topic="exercise_volume",
                text="start with small, achievable increases in activity rather than a large jump in volume",
                grounded_fact=GroundedFact(
                    claim=claim,
                    source_type="questionnaire",
                    source_ref="facts.exercise.aerobic_activity",
                    numeric_values=_numbers_in(claim),
                ),
            )
        )

    sleep = context.fact("mind.sleep_duration")
    stress = context.fact("mind.stress")
    sleep_triggered = bool(sleep and sleep.value in {"5_6_hours", "less_than_5_hours"})
    stress_triggered = bool(stress and stress.value == "high")
    if sleep_triggered or stress_triggered:
        # Same fix as the nutrition modifier above: build the claim from
        # only the signal(s) that actually triggered, not both unconditionally.
        reported_parts = []
        source_refs = []
        if sleep_triggered:
            reported_parts.append("short sleep duration")
            source_refs.append("facts.mind.sleep_duration")
        if stress_triggered:
            reported_parts.append("high stress")
            source_refs.append("facts.mind.stress")
        modifiers.append(
            QuestionnaireModifier(
                topic="pacing",
                text=f"keep the plan to a small number of simultaneous changes given reported {' and '.join(reported_parts)}",
                grounded_fact=GroundedFact(
                    claim=f"questionnaire reports {' and '.join(reported_parts)}",
                    source_type="questionnaire",
                    source_ref=",".join(source_refs),
                ),
            )
        )

    family_history_caution = context.caution("family_history_context")
    if family_history_caution is not None:
        # Same fix as the exercise-limitation modifier above: render the
        # caution's actual reported detail instead of a hardcoded "type 2
        # diabetes" claim that would be wrong for any other family-history
        # detail this caution kind might carry.
        modifiers.append(
            QuestionnaireModifier(
                topic="family_history",
                text=(
                    f"treat clinician follow-up as a bit more of a priority given your reported "
                    f"{_naturalize_detail(family_history_caution.detail)}, without treating it as "
                    "proof of a condition"
                ),
                grounded_fact=GroundedFact(
                    claim=f"questionnaire reports family history context: {family_history_caution.detail}",
                    source_type="questionnaire",
                    source_ref="cautions.family_history_context",
                    numeric_values=_numbers_in(family_history_caution.detail),
                ),
            )
        )

    return modifiers


_NEGATION_CUES = ("denies", "denied", "no ", "not ", "without", "negative for", "does not")


def _affirmatively_mentions(detail: str, keyword: str) -> bool:
    """True only if ``detail`` mentions ``keyword`` as a positive report,
    not a denial. A bare substring match on ``keyword`` alone used to treat
    "Patient denies levothyroxine use" the same as "Reports levothyroxine
    use" -- an independent review caught this. This is a coarse heuristic
    (a negation cue anywhere in the same short caution sentence suppresses
    the claim), not full negation parsing, but it makes silence the safe
    default instead of a confident wrong claim.
    """
    lowered = detail.lower()
    if keyword not in lowered:
        return False
    return not any(cue in lowered for cue in _NEGATION_CUES)


def build_supplement_cautions(context: QuestionnaireContext, profile: UserProfile) -> list[QuestionnaireModifier]:
    """Note the trigger condition for each caution below is deliberately
    the *specific* medication/allergy name, not just "some caution of this
    general kind exists." `has_caution_kind("medication_context")` alone
    used to be enough to trigger the levothyroxine-specific claim below,
    even though a `medication_context` caution about a completely
    different medication would make that claim false -- an independent
    review caught this ("policy written for one case getting applied to a
    different case by accident," exactly the failure mode
    `docs/AWS_ROADMAP.md`'s own process checklist calls out). Checking the
    actual caution's `detail` text (or the structured `profile.medications`/
    `profile.allergies` list) for the specific name means a future
    questionnaire with an unrelated `medication_context`/`allergy_context`
    caution correctly produces *no* claim here rather than a wrong one --
    silence is the safe failure mode, not a confident wrong claim.
    """
    cautions: list[QuestionnaireModifier] = []
    medication_caution = context.caution("medication_context")
    levothyroxine_reported = any(m.name == "levothyroxine" for m in profile.medications) or (
        medication_caution is not None and _affirmatively_mentions(medication_caution.detail, "levothyroxine")
    )
    if levothyroxine_reported:
        cautions.append(
            QuestionnaireModifier(
                topic="medication",
                text=(
                    "avoid supplement timing or dosing guidance because levothyroxine use is "
                    "reported and some products interact with thyroid medication routines"
                ),
                grounded_fact=GroundedFact(
                    claim="user reports levothyroxine use",
                    source_type="questionnaire",
                    source_ref="cautions.medication_context",
                ),
            )
        )

    allergy_caution = context.caution("allergy_context")
    shellfish_allergy_reported = any(a.name == "shellfish" for a in profile.allergies) or (
        allergy_caution is not None and _affirmatively_mentions(allergy_caution.detail, "shellfish")
    )
    if shellfish_allergy_reported:
        cautions.append(
            QuestionnaireModifier(
                topic="allergy",
                text="avoid assuming shellfish-derived supplement products are safe, given the reported shellfish allergy",
                grounded_fact=GroundedFact(
                    claim="user reports a shellfish allergy",
                    source_type="questionnaire",
                    source_ref="cautions.allergy_context",
                ),
            )
        )
    return cautions


def alcohol_unknown_limitation(context: QuestionnaireContext, focus_items: list[FocusItem]) -> Limitation | None:
    """Surface the unanswered alcohol question only when it materially matters.

    Policy (``kb_nutrition_007``): mention only if triglycerides are elevated.
    """
    unknown = next((u for u in context.unknowns if u.field == "nutrition.alcohol_intake"), None)
    if unknown is None:
        return None
    trig_flagged = any(it.marker.concept_id == "triglycerides_mg_dl" for it in focus_items)
    if not trig_flagged:
        return None
    return Limitation(
        kind="missing_context",
        detail="Alcohol intake was not answered in the questionnaire, and it can affect triglyceride results.",
    )


def staleness_limitation(latest_panel: Panel | None) -> tuple[Limitation | None, StalenessResult | None]:
    if latest_panel is None:
        return Limitation(kind="missing_data", detail="No bloodwork panel is available for this user."), None
    result = assess_staleness(latest_panel.measurement_date)
    if result.level == "fresh":
        return None, result
    if result.level == "potentially_stale":
        return (
            Limitation(
                kind="stale_data",
                detail=(
                    f"The latest panel is from {latest_panel.measurement_date} "
                    f"({result.age_days} days ago), which is old enough that newer labs "
                    "would give a more reliable priority read."
                ),
            ),
            result,
        )
    return (
        Limitation(
            kind="stale_data",
            detail=(
                f"The latest panel is from {latest_panel.measurement_date} "
                f"({result.age_days} days ago) and should be treated as stale; "
                "get updated labs before acting on this as a current priority."
            ),
        ),
        result,
    )


@dataclass
class Brief:
    """Structured, fully-grounded output of the reasoning pipeline."""

    intent: str
    focus_items: list[FocusItem] = field(default_factory=list)
    metabolic_pattern_detected: bool = False
    questionnaire_modifiers: list[QuestionnaireModifier] = field(default_factory=list)
    limitations: list[Limitation] = field(default_factory=list)
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)
    grounded_facts: list[GroundedFact] = field(default_factory=list)
    staleness: StalenessResult | None = None
    clinician_review_recommended: bool = False
    trend_result: TrendResult | None = None  # populated for trend_check intent
    supplement_cautions: list[QuestionnaireModifier] = field(default_factory=list)
    mentioned_concepts: list[str] = field(default_factory=list)
    mentioned_markers: dict[str, Biomarker] = field(default_factory=dict)
    previous_panel_dates: list[str] = field(default_factory=list)
    red_flag: bool = False
