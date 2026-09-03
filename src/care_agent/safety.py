"""Safety guardrails applied to every composed answer before it is returned.

Three independent checks, run in order:

1. ``check_no_diagnosis`` -- the answer must not assert the user *has* a
   diagnosable condition (diabetes, prediabetes, CVD, kidney/liver disease,
   etc.). Educational "range associated with higher risk" language is fine;
   "you have diabetes" is not.
2. ``check_no_dosing`` -- the answer must not give a supplement/medication
   dose, frequency, or timing instruction.
3. ``verify_numeric_grounding`` -- every standalone number that appears in
   the answer text must match a number carried by at least one
   ``GroundedFact`` collected during the pipeline. This is the concrete
   implementation of ``kb_grounding_002`` ("a generated value that is not
   present in the retrieved context is a grounding failure") and is what
   makes an optional LLM narration pass safe to use: even if the LLM
   paraphrases, it cannot introduce a new number without failing this check.

All three checks run regardless of which narrator backend produced the text,
so the guarantee holds for the deterministic template narrator and any LLM
narrator equally.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from care_agent.models import GroundedFact, SafetyCheck

_DIAGNOSIS_PATTERNS = [
    r"\byou have (type\s?[12]\s?)?diabetes\b",
    r"\byou have prediabetes\b",
    r"\byou are (pre)?diabetic\b",
    r"\byou have (heart|cardiovascular) disease\b",
    r"\byou have (kidney|renal) disease\b",
    r"\byou have (liver|hepatic) disease\b",
    r"\byou have insulin resistance\b",
    r"\byou('re| are) diagnosed with\b",
    r"\bthis (means|confirms) you have\b",
    r"\byour diagnosis is\b",
]

_DOSING_PATTERNS = [
    r"\btake \d+\s?(mg|mcg|iu|g|ml)\b",
    r"\b\d+\s?(mg|mcg|iu|g|ml)\s?(per day|daily|/day|a day|twice|once)\b",
    r"\bstart taking\b",
    r"\bstop taking\b",
    r"\bincrease your dose\b",
    r"\bdecrease your dose\b",
    r"\bswitch (your )?medication\b",
    r"\bchange your (dose|dosage|medication)\b",
]

_NUMBER_RE = re.compile(r"(?<![\w.])\d+\.?\d*(?![\w.])")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


@dataclass(frozen=True)
class SafetyReport:
    checks: tuple[SafetyCheck, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> tuple[SafetyCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def check_no_diagnosis(text: str) -> SafetyCheck:
    lowered = text.lower()
    for pattern in _DIAGNOSIS_PATTERNS:
        if re.search(pattern, lowered):
            return SafetyCheck(name="no_diagnosis", passed=False, detail=f"Matched forbidden pattern: {pattern!r}")
    return SafetyCheck(name="no_diagnosis", passed=True)


def check_no_dosing(text: str) -> SafetyCheck:
    lowered = text.lower()
    for pattern in _DOSING_PATTERNS:
        if re.search(pattern, lowered):
            return SafetyCheck(name="no_dosing", passed=False, detail=f"Matched forbidden pattern: {pattern!r}")
    return SafetyCheck(name="no_dosing", passed=True)


def verify_numeric_grounding(
    text: str,
    grounded_facts: list[GroundedFact],
    allowed_extra_numbers: set[float] | None = None,
    allowed_dates: set[str] | None = None,
) -> SafetyCheck:
    """Every number in ``text`` must appear in some grounded fact's numeric_values.

    ISO dates (``YYYY-MM-DD``) are checked separately against
    ``allowed_dates`` rather than digit-by-digit, so a legitimate date like
    "2026-05-06" doesn't get flagged for the standalone number "2026".

    ``allowed_extra_numbers`` covers numbers that are safe by construction and
    not tied to a single grounded fact -- specifically the small ordinal list
    markers ("1.", "2.", "3.") the composer itself uses to number a ranked
    list. It deliberately does not cover arbitrary numbers: any clinical
    value must still come from a ``GroundedFact``.
    """
    dates_in_text = set(_ISO_DATE_RE.findall(text))
    if allowed_dates is not None:
        ungrounded_dates = dates_in_text - allowed_dates
    else:
        ungrounded_dates = set()

    text_without_dates = _ISO_DATE_RE.sub(" ", text)

    allowed: set[float] = set()
    for fact in grounded_facts:
        allowed.update(fact.numeric_values)
    if allowed_extra_numbers:
        allowed.update(allowed_extra_numbers)

    ungrounded: list[str] = []
    for match in _NUMBER_RE.finditer(text_without_dates):
        raw = match.group()
        try:
            value = float(raw)
        except ValueError:
            continue
        if value not in allowed:
            ungrounded.append(raw)

    problems = []
    if ungrounded:
        problems.append(f"ungrounded numbers: {sorted(set(ungrounded))}")
    if ungrounded_dates:
        problems.append(f"ungrounded dates: {sorted(ungrounded_dates)}")

    if problems:
        return SafetyCheck(name="numeric_grounding", passed=False, detail="; ".join(problems))
    return SafetyCheck(name="numeric_grounding", passed=True)


def run_safety_checks(
    text: str,
    grounded_facts: list[GroundedFact],
    allowed_extra_numbers: set[float] | None = None,
    allowed_dates: set[str] | None = None,
) -> SafetyReport:
    checks = (
        check_no_diagnosis(text),
        check_no_dosing(text),
        verify_numeric_grounding(text, grounded_facts, allowed_extra_numbers, allowed_dates),
    )
    return SafetyReport(checks=checks)
