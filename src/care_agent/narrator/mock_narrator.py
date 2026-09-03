"""Deterministic, dependency-free narrator.

This is the default narrator and the one all tests run against. It is plain
Python string templating over the ``Brief`` -- no model call, no randomness,
no network. It exists to satisfy this project's constraint that the
solution "should still be possible to review and test without relying on
external paid APIs" as the *primary* path, not a fallback: every number and
claim it emits was already computed by ``reasoning.py``, so this module only
has to choose good phrasing, never new facts.
"""

from __future__ import annotations

from care_agent.intent import PRIORITY_FOCUS, RED_FLAG, SUPPLEMENT_SAFETY, TREND_CHECK
from care_agent.models import UserProfile
from care_agent.reasoning import Brief, FocusItem

_TOP_N_FOCUS = 3


def _fmt_marker(item: FocusItem) -> str:
    cls = (item.marker.classification or "unclassified").replace("_", " ")
    return f"{item.marker.display_name} {item.marker.value} {item.marker.unit} ({cls})"


def _sources_line(brief: Brief) -> str:
    seen: dict[str, str] = {}
    for rc in brief.retrieved_chunks:
        seen.setdefault(rc.chunk.source_name, rc.chunk.source_url)
    if not seen:
        return ""
    parts = [f"{name} ({url})" for name, url in list(seen.items())[:5]]
    return "Sources: " + "; ".join(parts)


def _next_steps(brief: Brief) -> list[str]:
    action_fields: set[str] = set()
    for item in brief.focus_items[:_TOP_N_FOCUS]:
        action_fields.update(item.marker.action_fields)

    steps: list[str] = []
    if "NUTRITION" in action_fields:
        mod = next((m for m in brief.questionnaire_modifiers if m.topic == "nutrition"), None)
        if mod:
            steps.append(f"Nutrition: {mod.text}.")
        else:
            steps.append(
                "Nutrition: shift toward vegetables, whole grains, and less added sugar (a DASH/heart-healthy style eating pattern)."
            )
    if "EXERCISE" in action_fields:
        ex = next((m for m in brief.questionnaire_modifiers if m.topic == "exercise"), None)
        vol = next((m for m in brief.questionnaire_modifiers if m.topic == "exercise_volume"), None)
        parts = [m.text for m in (ex, vol) if m]
        if parts:
            steps.append("Exercise: " + "; ".join(parts) + ".")
        else:
            steps.append("Exercise: build toward regular moderate activity, per general adult activity guidance.")
    if "MEDICAL" in action_fields:
        extra = " — worth prioritizing since LDL and HbA1c are elevated together" if brief.clinician_review_recommended else ""
        steps.append(f"Clinician review: bring these results to your clinician for interpretation{extra}.")

    pacing = next((m for m in brief.questionnaire_modifiers if m.topic == "pacing"), None)
    if pacing:
        steps.append(f"Pace: {pacing.text}.")

    fh = next((m for m in brief.questionnaire_modifiers if m.topic == "family_history"), None)
    if fh:
        steps.append(f"Context: {fh.text}.")

    return steps


def _compose_priority_focus(brief: Brief, question_text: str, profile: UserProfile) -> str:
    lines: list[str] = []
    top = brief.focus_items[:_TOP_N_FOCUS]
    rest = brief.focus_items[_TOP_N_FOCUS:]

    if not top:
        lines.append(
            "Nothing in your latest panel is flagged above an adequate/optimal range, so there's no priority marker to rank right now."
        )
    else:
        lines.append("Based on your latest bloodwork, here's what stands out, ranked:")
        for i, item in enumerate(top, start=1):
            lines.append(f"{i}. {_fmt_marker(item)}")

        if brief.metabolic_pattern_detected:
            lines.append(
                "HbA1c, fasting glucose, and triglycerides are elevated or borderline together in this panel, "
                "which supports treating this as a broader metabolic pattern rather than isolated numbers — "
                "nutrition, activity, sleep, and stress all plausibly move the whole cluster."
            )

        if rest:
            extra = ", ".join(f"{it.marker.display_name} ({(it.marker.classification or '').replace('_', ' ')})" for it in rest)
            lines.append(f"Also flagged for review, lower priority right now: {extra}.")

        lines.append(
            "These values sit in ranges commonly associated with higher cardiometabolic risk. "
            "This is not a diagnosis, and it doesn't replace a clinician's interpretation of your full history."
        )

    steps = _next_steps(brief)
    if steps:
        lines.append("Next steps:")
        lines.extend(f"- {s}" for s in steps)

    if brief.questionnaire_modifiers:
        lines.append(
            "Your questionnaire answers changed this plan: it steers toward low-impact movement given knee pain, "
            "keeps the number of simultaneous changes small given reported sleep and stress, and leans on your "
            "stated food and activity preferences instead of a generic plan."
        )

    for lim in brief.limitations:
        lines.append(f"Limitation: {lim.detail}")

    src = _sources_line(brief)
    if src:
        lines.append(src)

    return "\n".join(lines)


def _compose_trend(brief: Brief, question_text: str, profile: UserProfile) -> str:
    tr = brief.trend_result
    lines: list[str] = []
    if tr is None:
        lines.append(
            "I couldn't identify which marker you're asking about from your question. "
            "Could you name the specific lab (e.g. LDL, HbA1c, fasting glucose)?"
        )
        return "\n".join(lines)

    display = brief.mentioned_markers.get(tr.concept_id)
    display_name = display.display_name if display else tr.concept_id

    if tr.available:
        assert tr.direction is not None  # guaranteed by TrendResult when available=True
        direction_word = {"up": "higher than", "down": "lower than", "flat": "the same as"}[tr.direction]
        lines.append(
            f"Your {display_name} was {tr.latest_value} {tr.unit} on {tr.latest_date}, "
            f"{direction_word} the {tr.previous_value} {tr.unit} result from {tr.previous_date}."
        )
    else:
        if tr.latest_value is not None:
            lines.append(f"Your latest {display_name} result is {tr.latest_value} {tr.unit} from {tr.latest_date}.")
        lines.append(tr.reason_unavailable or "A trend can't be determined from the available data.")
        if brief.previous_panel_dates:
            lines.append(
                f"I checked your previous panel(s) ({', '.join(brief.previous_panel_dates)}) as well; "
                "they don't include a comparable dated measurement for this marker."
            )

    for lim in brief.limitations:
        lines.append(f"Limitation: {lim.detail}")

    src = _sources_line(brief)
    if src:
        lines.append(src)

    return "\n".join(lines)


def _compose_supplement(brief: Brief, question_text: str, profile: UserProfile) -> str:
    lines: list[str] = []

    if brief.mentioned_markers:
        for marker in brief.mentioned_markers.values():
            cls = (marker.classification or "unclassified").replace("_", " ")
            lines.append(f"Your latest {marker.display_name} is {marker.value} {marker.unit} ({cls}).")

    lines.append(
        "I can't recommend a specific supplement, dose, or timing here. Supplement choices depend on your "
        "full medication list, allergies, kidney/liver context, and clinician or pharmacist input."
    )

    for caution in brief.supplement_cautions:
        lines.append(f"Also relevant: {caution.text}.")

    lines.append(
        "General, non-personalized education: heart-healthy eating patterns (like DASH) and regular activity "
        "are the first-line habits usually discussed alongside cholesterol results. A clinician or pharmacist "
        "can advise on whether a supplement is appropriate for you specifically."
    )

    for lim in brief.limitations:
        lines.append(f"Limitation: {lim.detail}")

    src = _sources_line(brief)
    if src:
        lines.append(src)

    return "\n".join(lines)


def _compose_red_flag(brief: Brief, question_text: str, profile: UserProfile) -> str:
    return (
        "This sounds like it could be urgent. Please seek immediate medical help now "
        "(emergency services or an emergency room) rather than waiting on lab-based advice here. "
        "I'm not able to triage acute symptoms."
    )


def _compose_general(brief: Brief, question_text: str, profile: UserProfile) -> str:
    lines: list[str] = []
    if brief.mentioned_markers:
        for marker in brief.mentioned_markers.values():
            cls = (marker.classification or "unclassified").replace("_", " ")
            lines.append(f"Your latest {marker.display_name} is {marker.value} {marker.unit} ({cls}).")
    elif brief.focus_items:
        top = brief.focus_items[:_TOP_N_FOCUS]
        lines.append("Here's what's flagged in your latest panel:")
        for item in top:
            lines.append(f"- {_fmt_marker(item)}")
    else:
        lines.append("I don't have enough grounded data to answer that specifically yet.")

    lines.append("This is general, educational information based on your data — not a diagnosis or treatment plan.")

    for lim in brief.limitations:
        lines.append(f"Limitation: {lim.detail}")

    src = _sources_line(brief)
    if src:
        lines.append(src)

    return "\n".join(lines)


class MockNarrator:
    """Default, dependency-free narrator used everywhere tests run."""

    backend_name = "mock"

    def compose(self, brief: Brief, question_text: str, profile: UserProfile) -> str:
        if brief.red_flag:
            return _compose_red_flag(brief, question_text, profile)
        if brief.intent == PRIORITY_FOCUS:
            return _compose_priority_focus(brief, question_text, profile)
        if brief.intent == TREND_CHECK:
            return _compose_trend(brief, question_text, profile)
        if brief.intent == SUPPLEMENT_SAFETY:
            return _compose_supplement(brief, question_text, profile)
        if brief.intent == RED_FLAG:
            return _compose_red_flag(brief, question_text, profile)
        return _compose_general(brief, question_text, profile)
