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

    if context.has_caution_kind("exercise_limitation"):
        pref = next((p for p in context.preferences if p.field == "exercise.preference"), None)
        pref_text = f" ({pref.value.replace('_', ' ')} preferred)" if pref else ""
        modifiers.append(
            QuestionnaireModifier(
                topic="exercise",
                text=f"prefer low-impact activity{pref_text} over running or jumping, given reported knee pain",
                grounded_fact=GroundedFact(
                    claim="questionnaire reports knee pain with running/jumping",
                    source_type="questionnaire",
                    source_ref="cautions.exercise_limitation",
                ),
            )
        )

    sugary = context.fact("nutrition.sugary_foods")
    veg = context.fact("nutrition.vegetables")
    if sugary and sugary.value in {"3_4_days_per_week", "5_6_days_per_week", "daily"} or (veg and veg.value in {"0_1_servings_per_day"}):
        pref = next((p for p in context.preferences if p.field == "nutrition.preference"), None)
        pref_text = f", leaning on {pref.value.replace('_', ' ')}" if pref else ""
        modifiers.append(
            QuestionnaireModifier(
                topic="nutrition",
                text=f"prioritize reducing sugary foods and adding vegetables{pref_text}",
                grounded_fact=GroundedFact(
                    claim="questionnaire reports frequent sugary foods and low vegetable intake",
                    source_type="questionnaire",
                    source_ref="facts.nutrition.sugary_foods,facts.nutrition.vegetables",
                ),
            )
        )

    aerobic = context.fact("exercise.aerobic_activity")
    if aerobic and aerobic.value == "less_than_60_min_per_week":
        modifiers.append(
            QuestionnaireModifier(
                topic="exercise_volume",
                text="start with small, achievable increases in activity rather than a large jump in volume",
                grounded_fact=GroundedFact(
                    claim="questionnaire reports less than 60 minutes of aerobic activity per week",
                    source_type="questionnaire",
                    source_ref="facts.exercise.aerobic_activity",
                ),
            )
        )

    sleep = context.fact("mind.sleep_duration")
    stress = context.fact("mind.stress")
    if (sleep and sleep.value in {"5_6_hours", "less_than_5_hours"}) or (stress and stress.value == "high"):
        modifiers.append(
            QuestionnaireModifier(
                topic="pacing",
                text="keep the plan to a small number of simultaneous changes given reported short sleep and high stress",
                grounded_fact=GroundedFact(
                    claim="questionnaire reports short sleep duration and high stress",
                    source_type="questionnaire",
                    source_ref="facts.mind.sleep_duration,facts.mind.stress",
                ),
            )
        )

    if context.has_caution_kind("family_history_context"):
        modifiers.append(
            QuestionnaireModifier(
                topic="family_history",
                text=(
                    "treat clinician follow-up as a bit more of a priority given the reported "
                    "family history, without treating it as proof of a condition"
                ),
                grounded_fact=GroundedFact(
                    claim="questionnaire reports first-degree family history of type 2 diabetes",
                    source_type="questionnaire",
                    source_ref="cautions.family_history_context",
                ),
            )
        )

    return modifiers


def build_supplement_cautions(context: QuestionnaireContext, profile: UserProfile) -> list[QuestionnaireModifier]:
    cautions: list[QuestionnaireModifier] = []
    if context.has_caution_kind("medication_context") or any(m.name == "levothyroxine" for m in profile.medications):
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
    if context.has_caution_kind("allergy_context") or any(a.name == "shellfish" for a in profile.allergies):
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
