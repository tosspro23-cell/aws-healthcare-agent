"""Lightweight rule-based intent classification.

A full intent/slot classifier is out of scope for this project, and an LLM
call isn't needed either: the three sample question types are lexically
distinct enough that keyword rules are transparent, deterministic, and
100% testable. Unmatched questions fall back to "general_bloodwork_question"
so the agent still answers (grounded, with limitations) instead of refusing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

Intent = str

PRIORITY_FOCUS: Intent = "priority_focus"
TREND_CHECK: Intent = "trend_check"
SUPPLEMENT_SAFETY: Intent = "supplement_safety"
GENERAL: Intent = "general_bloodwork_question"
RED_FLAG: Intent = "red_flag_emergency"

_RED_FLAG_PATTERNS = [
    r"\bchest pain\b",
    r"\bsevere shortness of breath\b",
    r"\bcan'?t breathe\b",
    r"\bfainting\b",
    r"\bfainted\b",
    r"\bpassed out\b",
    r"\bsigns? of stroke\b",
    r"\bface drooping\b",
    r"\bsuicidal\b",
    r"\bsuicide\b",
    r"\bwant to die\b",
    r"\brapidly worsening\b",
]

_SUPPLEMENT_PATTERNS = [r"\bsupplement", r"\bdose\b", r"\bdosage\b", r"\bpill\b", r"\bmg\b of\b"]
# A bare mention of "vitamin" is a weaker signal than the patterns above --
# "Vitamin D" is also a biomarker's *name*, so "Has my vitamin D changed
# since last time?" used to get force-classified as supplement_safety
# before trend_check ever got a chance, purely because the marker's own
# name contains this word. Found live testing the Workbench: the
# resulting answer never ran trend computation at all, and the LLM
# narrator filled the gap with an unverified claim about data
# availability that happened to be true by coincidence, not because
# anything actually checked it. Kept as its own pattern list (not folded
# into `_TREND_PATTERNS`) because a genuine supplement question like "what
# vitamin should I take?" should still be supplement_safety when no
# trend/priority language is also present.
_MARKER_NAME_ONLY_PATTERNS = [r"\bvitamin\b"]
_TREND_PATTERNS = [
    r"\bworse\b",
    r"\bbetter\b",
    r"\bimprov",
    r"\btrend\b",
    r"\bchanged?\b",
    r"\bcompared? to\b",
    r"\bsince (last|my previous)\b",
    r"\bgoing up\b",
    r"\bgoing down\b",
]
_PRIORITY_PATTERNS = [
    r"\bfocus on\b",
    r"\bwhat should i\b",
    r"\bpriorit",
    r"\bfirst\b",
    r"\bwhere do i start\b",
]


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    matched_patterns: tuple[str, ...]


def _matches(text: str, patterns: list[str]) -> list[str]:
    hits = []
    for p in patterns:
        if re.search(p, text):
            hits.append(p)
    return hits


def classify(question_text: str) -> IntentResult:
    text = question_text.lower()

    red_flag_hits = _matches(text, _RED_FLAG_PATTERNS)
    if red_flag_hits:
        return IntentResult(intent=RED_FLAG, matched_patterns=tuple(red_flag_hits))

    supplement_hits = _matches(text, _SUPPLEMENT_PATTERNS)
    if supplement_hits:
        return IntentResult(intent=SUPPLEMENT_SAFETY, matched_patterns=tuple(supplement_hits))

    trend_hits = _matches(text, _TREND_PATTERNS)
    priority_hits = _matches(text, _PRIORITY_PATTERNS)
    marker_name_hits = _matches(text, _MARKER_NAME_ONLY_PATTERNS)

    # A marker-name-only mention (just "vitamin", no stronger supplement
    # signal) only wins as supplement_safety when trend/priority language
    # isn't also present -- otherwise "has my vitamin D changed since last
    # time" would never reach the trend branch below.
    if marker_name_hits and not trend_hits and not priority_hits:
        return IntentResult(intent=SUPPLEMENT_SAFETY, matched_patterns=tuple(marker_name_hits))

    if trend_hits and not priority_hits:
        return IntentResult(intent=TREND_CHECK, matched_patterns=tuple(trend_hits))
    if priority_hits:
        return IntentResult(intent=PRIORITY_FOCUS, matched_patterns=tuple(priority_hits))
    if trend_hits:
        return IntentResult(intent=TREND_CHECK, matched_patterns=tuple(trend_hits))

    return IntentResult(intent=GENERAL, matched_patterns=())
