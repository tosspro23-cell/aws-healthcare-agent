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


_CLAIM_PREFIX = "questionnaire reports "


def _claim_detail(brief: Brief, topic: str) -> str | None:
    """The specific, already-correct detail from a modifier's own
    grounded-fact claim (built in `reasoning.py` from only the
    sub-signal(s) that actually triggered it), for reuse here instead of
    a second, separately-hardcoded phrase. A second independent review
    found that this function's *first* fix (checking which topics fired)
    still hardcoded which *sub-signal* fired within a topic that can be
    triggered by more than one -- "pacing" can fire from short sleep,
    high stress, or both, but the summary always named "sleep and
    stress" regardless. Reusing the claim (already fixed to name only
    what triggered) instead of a second hardcoded phrase means the two
    can't drift apart from each other again the same way."""
    modifier = next((m for m in brief.questionnaire_modifiers if m.topic == topic), None)
    if modifier is None:
        return None
    claim = modifier.grounded_fact.claim
    return claim[len(_CLAIM_PREFIX) :] if claim.startswith(_CLAIM_PREFIX) else None


def _personalization_summary(brief: Brief) -> str | None:
    """Built from whichever questionnaire modifiers actually fired this
    time, not a fixed sentence naming every possible personalization this
    project supports -- raised directly against a live Workbench answer:
    the old version unconditionally claimed the plan "steers toward
    low-impact movement given knee pain" and "keeps changes small given
    sleep and stress" whenever *any* modifier was present, regardless of
    whether those specific ones actually fired. Only coincidentally
    correct with the shipped sample data, where every modifier always
    fires together -- the same hardcoded-regardless-of-trigger failure
    mode already fixed elsewhere in this file's callers (see
    docs/DECISIONS.md)."""
    topics = {m.topic for m in brief.questionnaire_modifiers}
    if not topics:
        return None
    parts = []
    if "exercise" in topics:
        parts.append("steers toward accommodations for your reported exercise limitation")
    if "pacing" in topics:
        detail = _claim_detail(brief, "pacing")
        if detail:
            parts.append(f"keeps the number of simultaneous changes small given your reported {detail}")
        else:
            parts.append("paces changes given what you reported")
    if "nutrition" in topics:
        # Deliberately its own branch, not combined with exercise_volume
        # below -- a second independent review found the combined version
        # claimed "food and activity preferences" even when only one of
        # the two (a dietary signal, or a low-activity-volume signal) had
        # actually fired, misnaming the other every time.
        detail = _claim_detail(brief, "nutrition")
        parts.append(f"prioritizes your reported {detail}" if detail else "leans on your stated food preferences")
    if "exercise_volume" in topics:
        detail = _claim_detail(brief, "exercise_volume")
        parts.append(f"builds up activity gradually given your reported {detail}" if detail else "builds up activity gradually")
    if "family_history" in topics:
        parts.append("gives your reported family history a bit more weight when suggesting clinician follow-up")
    if not parts:
        return None
    return f"Your questionnaire answers changed this plan: it {_join_naturally(parts)}."


def _join_naturally(parts: list[str]) -> str:
    """ "A, B, and C" rather than "; and "-joining every part regardless of
    count -- the latter reads mechanically once there are 3+ parts, which
    is exactly the "still feels templated" feedback that motivated
    breaking this sentence up into parts in the first place."""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


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

    personalization = _personalization_summary(brief)
    if personalization:
        lines.append(personalization)

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
