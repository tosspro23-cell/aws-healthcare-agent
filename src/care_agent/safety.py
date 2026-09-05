"""Safety guardrails applied to every composed answer before it is returned.

Four independent checks, run in order:

1. ``check_non_empty`` -- the answer must actually contain text. An empty
   or whitespace-only answer trivially "passes" every other check (no
   diagnosis pattern matches nothing, no number to be ungrounded) without
   being a safe, useful response.
2. ``check_no_diagnosis`` -- the answer must not assert the user *has* a
   diagnosable condition (diabetes, prediabetes, CVD, kidney/liver disease,
   etc.). Educational "range associated with higher risk" language is fine;
   "you have diabetes" is not.
3. ``check_no_dosing`` -- the answer must not give a supplement/medication
   dose, frequency, or timing instruction.
4. ``verify_numeric_grounding`` -- every number that appears attached to a
   known unit (e.g. "162 mg/dL") must match a ``GroundedFact`` carrying
   that *same value and unit* -- not just the same value attached to any
   marker. Every other standalone number must still match some grounded
   fact's numeric value. This is the concrete implementation of
   ``kb_grounding_002`` ("a generated value that is not present in the
   retrieved context is a grounding failure") and is what makes an
   optional LLM narration pass safe to use: even if the LLM paraphrases,
   it cannot introduce a new number, or reattach a real number to the
   wrong marker, without failing this check.

All four checks run regardless of which narrator backend produced the
text, so the same rules apply to the deterministic template narrator and
any LLM narrator equally.

**Honest limits, not a claim of completeness** (see
``docs/INDEPENDENT_REVIEW_FINDINGS.md``, finding #4, for the specific
counterexamples that motivated this file's current shape): checks 2 and 3
are pattern-based over English phrasing. They cover the phrasings tested
here and in ``tests/test_safety.py``, and were expanded to catch several
real bypasses an independent review found -- but pattern matching over
free text cannot be made complete against a sufficiently creative
paraphrase. Check 4's value+unit binding closes the specific "real number,
wrong marker" bypass, but a number attached to a unit *not* in
``_KNOWN_UNITS`` still only gets the weaker value-only check. None of this
is a substitute for ``agent.py``'s existing fallback-to-mock-narrator
behavior on any check failure, which remains the actual safety net.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from care_agent.models import GroundedFact, SafetyCheck

_CONDITIONS = (
    r"(?:type\s?[12]\s?diabetes|diabetes|prediabetes|(?:heart|cardiovascular) disease|"
    r"(?:kidney|renal) disease|(?:liver|hepatic) disease|insulin resistance|metabolic syndrome)"
)

_DIAGNOSIS_PATTERNS = [
    rf"\byou (have|are) (a |an )?{_CONDITIONS}\b",
    r"\byou are (pre)?diabetic\b",
    r"\byou('re| are) diagnosed with\b",
    r"\byou('ve| have) been diagnosed with\b",
    r"\bthis (means|confirms) you have\b",
    r"\byour diagnosis is\b",
    r"\byour condition is\b",
    rf"\b{_CONDITIONS} is your (confirmed |diagnosed )?condition\b",
    rf"\byour (confirmed |diagnosed )?condition is {_CONDITIONS}\b",
]

_DOSAGE_FORMS = r"(?:capsule|tablet|pill|softgel|gummy|dose)"
_FREQUENCY_WORDS = r"(?:every morning|every evening|every night|each morning|each evening|daily|once a day|twice a day|three times a day)"

_DOSING_PATTERNS = [
    r"\btake \d+\s?(mg|mcg|iu|g|ml)\b",
    r"\b\d+\s?(mg|mcg|iu|g|ml)\s?(per day|daily|/day|a day|twice|once)\b",
    r"\bstart taking\b",
    r"\bstop taking\b",
    r"\bincrease your dose\b",
    r"\bdecrease your dose\b",
    r"\bswitch (your )?medication\b",
    r"\bchange your (dose|dosage|medication)\b",
    rf"\b(swallow|take) (one|two|three|a|an) {_DOSAGE_FORMS}\b",
    rf"\b{_DOSAGE_FORMS}\b.{{0,40}}\b{_FREQUENCY_WORDS}\b",
    rf"\b{_FREQUENCY_WORDS}\b.{{0,40}}\b{_DOSAGE_FORMS}\b",
]

# The real, complete unit vocabulary this project's sample data actually
# uses (verified against every marker in data/sample_bloodwork.json, not
# guessed) -- adding a new marker type with a different unit requires
# adding it here too, or numbers attached to that unit only get the
# weaker value-only check below.
_KNOWN_UNITS = ("mg/dL", "mg/L", "ng/mL", "mIU/L", "mL/min/1.73m2", "U/L", "%")
# `(?!\w)` rather than `\b` after the unit: `\b` requires a transition
# between a word and non-word character, which fails right after "%" when
# the next character is *also* non-word (e.g. the "." in "162%.") -- a
# real bug caught by the test suite, not a hypothetical one.
_VALUE_UNIT_RE = re.compile(r"(\d+\.?\d*)\s?(" + "|".join(re.escape(u) for u in _KNOWN_UNITS) + r")(?!\w)", re.IGNORECASE)

# No longer requires a non-word/non-period lookahead after the digits --
# that used to make "999mg" (no space before the unit) invisible to this
# check entirely, since a following letter blocked the match. Leading
# lookbehind is unchanged: still won't match the "006" inside an
# identifier like "kb_a1c_006", since "_" is a word character.
_NUMBER_RE = re.compile(r"(?<![\w.])\d+\.?\d*")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_ORDINAL_LIST_MARKER_RE = re.compile(r"(?m)^\s*(\d+\.)\s")


@dataclass(frozen=True)
class SafetyReport:
    checks: tuple[SafetyCheck, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> tuple[SafetyCheck, ...]:
        return tuple(c for c in self.checks if not c.passed)


def check_non_empty(text: str) -> SafetyCheck:
    if not text or not text.strip():
        return SafetyCheck(name="non_empty", passed=False, detail="Answer text is empty or whitespace-only.")
    return SafetyCheck(name="non_empty", passed=True)


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
    allowed_dates: set[str] | None = None,
) -> SafetyCheck:
    """Every number in ``text`` must be grounded, at the strongest level
    binding that's actually verifiable:

    - A number immediately followed by a recognized unit (``_KNOWN_UNITS``)
      must match a ``GroundedFact`` with that *same* (value, unit) pair --
      not just the same value attached to some other marker's fact. This
      is what catches "Your HbA1c is 162%" when 162 is only ever grounded
      as an LDL-C value in mg/dL: the value alone isn't enough evidence
      that it's attached to the right marker.
    - Every other number (no recognized unit immediately adjacent, or an
      ordinal list marker like "1. " at the start of a line -- see
      ``_ORDINAL_LIST_MARKER_RE``) falls back to the weaker check: it must
      match *some* grounded fact's numeric value, full stop. This is
      unavoidable for numbers with no unit to bind against (a plain "3
      things to focus on" has no marker to check it against) or units this
      project doesn't yet recognize.

    ISO dates (``YYYY-MM-DD``) are checked separately against
    ``allowed_dates`` rather than digit-by-digit, so a legitimate date like
    "2026-05-06" doesn't get flagged for the standalone number "2026".

    Ordinal list markers ("1. ", "2. ") are exempted only at the *exact
    position* they appear as a line-leading list marker -- not as a
    standing exception for that numeral anywhere else in the text. A
    composer numbering a 5-item list no longer makes "5" a safe number to
    attach to an invented clinical value elsewhere in the same answer.
    """
    dates_in_text = set(_ISO_DATE_RE.findall(text))
    if allowed_dates is not None:
        ungrounded_dates = dates_in_text - allowed_dates
    else:
        ungrounded_dates = set()

    text_without_dates = _ISO_DATE_RE.sub(" ", text)

    allowed_values: set[float] = set()
    allowed_value_unit_pairs: set[tuple[float, str]] = set()
    for fact in grounded_facts:
        allowed_values.update(fact.numeric_values)
        if fact.unit and fact.numeric_values:
            allowed_value_unit_pairs.update((value, fact.unit.strip().lower()) for value in fact.numeric_values)

    ordinal_spans = {m.span(1) for m in _ORDINAL_LIST_MARKER_RE.finditer(text_without_dates)}

    ungrounded: list[str] = []
    consumed_spans: list[tuple[int, int]] = []

    for match in _VALUE_UNIT_RE.finditer(text_without_dates):
        raw_value, raw_unit = match.group(1), match.group(2)
        consumed_spans.append(match.span(1))
        try:
            value = float(raw_value)
        except ValueError:
            continue
        if (value, raw_unit.strip().lower()) not in allowed_value_unit_pairs:
            ungrounded.append(f"{raw_value}{raw_unit}")

    for match in _NUMBER_RE.finditer(text_without_dates):
        span = match.span()
        if span in ordinal_spans or span in consumed_spans:
            continue
        raw = match.group()
        try:
            value = float(raw)
        except ValueError:
            continue
        if value not in allowed_values:
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
    allowed_dates: set[str] | None = None,
) -> SafetyReport:
    checks = (
        check_non_empty(text),
        check_no_diagnosis(text),
        check_no_dosing(text),
        verify_numeric_grounding(text, grounded_facts, allowed_dates),
    )
    return SafetyReport(checks=checks)
