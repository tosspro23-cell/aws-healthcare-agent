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
   marker -- and, when that fact carries a ``display_name`` (see
   ``GroundedFact``'s own docstring), the *correct marker's name* must
   also appear nearby, closing the narrower "right value and unit, wrong
   marker" gap that (value, unit) matching alone still leaves open when
   two markers share a unit. Every other standalone number must still
   match some grounded fact's numeric value. This is the concrete
   implementation of ``kb_grounding_002`` ("a generated value that is not
   present in the retrieved context is a grounding failure") and is what
   makes an optional LLM narration pass safe to use: even if the LLM
   paraphrases, it cannot introduce a new number, or reattach a real
   number to the wrong marker, without failing this check.

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
paraphrase. Check 4's value+unit binding, plus the marker-name check
described above, closes the "real number, wrong marker" bypass for any
fact carrying a ``display_name`` -- but a number attached to a unit *not*
in ``_KNOWN_UNITS``, or a fact with no ``display_name`` to check (e.g. a
panel-age or questionnaire-derived fact, which has no single "marker
name" to begin with), still only gets the weaker value-only check. None
of this is a substitute for ``agent.py``'s existing fallback-to-mock-
narrator behavior on any check failure, which remains the actual safety
net.
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
# the next character is *also* non-word (e.g. the "." in "162%."). The
# leading `-?` (a second independent review found this missing) captures
# a genuine negative sign so "-162 mg/dL" is checked as -162, not silently
# reinterpreted as the unsigned 162 -- without it, a fabricated negative
# value could slip past by reusing a real positive grounded number.
_VALUE_UNIT_RE = re.compile(r"(-?\d+\.?\d*)\s?(" + "|".join(re.escape(u) for u in _KNOWN_UNITS) + r")(?!\w)", re.IGNORECASE)

# No longer requires a non-word/non-period lookahead after the digits --
# that used to make "999mg" (no space before the unit) invisible to this
# check entirely, since a following letter blocked the match. Leading
# lookbehind is unchanged: still won't match the "006" inside an
# identifier like "kb_a1c_006", since "_" is a word character -- and still
# won't treat the hyphen in a hyphenated identifier ("test-162") as a sign,
# since the position right after a word character is excluded the same way
# it always was; `-?` only ever captures a hyphen preceded by whitespace,
# punctuation, or the start of the text.
_NUMBER_RE = re.compile(r"(?<![\w.])-?\d+\.?\d*")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# `[*_]{0,2}` tolerates a numbered list item wrapped in Markdown emphasis
# ("**1. See your clinician**"), not just a bare "1. " -- caught live
# against a real Bedrock answer (a manual post-deploy smoke test of the
# SQS queue path, not a hypothetical): the ordinal marker itself was
# bolded, which doesn't change that it's still a list marker, but did
# make it invisible to this regex, so "1", "3", "4" were rejected as
# ungrounded bare numbers even though they were never claiming to be
# clinical values -- the safety pipeline's own fallback caught this
# correctly (the mock template was served instead), but the underlying
# false positive is worth closing so real Bedrock answers stop being
# needlessly discarded for this reason.
_ORDINAL_LIST_MARKER_RE = re.compile(r"(?m)^\s*[*_]{0,2}(\d+\.)\s")

# Boundary characters for the marker-name proximity window used by
# verify_numeric_grounding's cross-marker check (below): the *current
# sentence or line*, not a fixed character count. A fixed window
# (originally 40 chars back / 20 forward) turned out too narrow for a
# real, legitimate phrasing this project's own deterministic narrator
# produces -- "Your LDL-C was 162 mg/dL on 2026-05-06, higher than the
# 148 mg/dL result from 2025-12-08." names the marker once, 51 characters
# before the second value -- caught live by the eval harness
# (`care_agent.eval`, `q_trend_available`) the first time it ran against
# this exact question, not by a hypothetical worry. Scoping to the
# sentence/line instead handles both directions correctly: a long,
# comma-heavy sentence naming its marker once at the start still keeps
# every value in that sentence in scope, while a bulleted, one-marker-
# per-line answer (the priority_focus narrator's actual shape) keeps
# each line's value from seeing a *different* marker's name on the
# adjacent line -- which a much wider fixed window would have let bleed
# through, undoing the cross-marker fix this window exists for in the
# first place. `_CONTEXT_MAX_CHARS` is a backstop for text with no
# sentence-ending punctuation or newline at all, not the normal case.
_SENTENCE_BOUNDARY_CHARS = ".!?\n"
_CONTEXT_MAX_CHARS = 200


def _sentence_context(text: str, start: int, end: int) -> str:
    """The current sentence/line around `text[start:end]`: expands outward
    to the nearest preceding and following sentence-ending punctuation or
    newline, capped at `_CONTEXT_MAX_CHARS` in each direction."""
    left_cap = max(0, start - _CONTEXT_MAX_CHARS)
    left = left_cap
    for i in range(start - 1, left_cap - 1, -1):
        if text[i] in _SENTENCE_BOUNDARY_CHARS:
            left = i + 1
            break

    right_cap = min(len(text), end + _CONTEXT_MAX_CHARS)
    right = right_cap
    for i in range(end, right_cap):
        if text[i] in _SENTENCE_BOUNDARY_CHARS:
            right = i
            break

    return text[left:right]


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

    **Deliberately does *not* exempt numbers that appear in the original
    question**, despite that having been tried: a version of this function
    briefly accepted a bare number as grounded if the caller's own
    question already used it, to stop a real false positive (an LLM
    narrator correctly *declining* to fabricate a "10-year cardiovascular
    risk score" was rejected purely because "10" -- from the user's own
    "10-year" phrasing -- matched no grounded fact). A second independent
    review found that this reopened a real fabrication bypass: it can't
    distinguish a model *declining* while referencing the question's
    number from a model *affirming* a fabricated value that happens to
    reuse it ("Is my risk score 999?" -> "Your... risk score is 999." now
    passed). It also weakened the *strict* value+unit path indirectly --
    not by design, but because irregular spacing ("500  mg/dL", two
    spaces) or Markdown emphasis ("**500** mg/dL") makes the value+unit
    regex fail to match, so the number falls through to the weak
    (now-exempted) path instead of being checked against real grounded
    values at all. Reverted rather than patched further: reliably telling
    "the model is declining while citing a number" from "the model is
    asserting that number as fact" isn't solvable with a regex, and the
    asymmetry matters -- a false positive here just means a safe answer
    gets replaced by the deterministic template; a false negative means a
    fabricated clinical number reaches the user. See docs/DECISIONS.md.

    **Cross-marker binding**: a (value, unit) pair matching *some*
    grounded fact used to be accepted regardless of which marker the text
    actually named -- several markers share a unit (LDL-C, HDL-C,
    triglycerides, and fasting glucose are all ``mg/dL``), so "Your LDL-C
    is 150 mg/dL" passed even when 150 is only ever grounded as
    Triglycerides. A second independent review found this open and left
    it as a deliberate backlog item rather than a quick patch; closed
    here without a full structured-claim rewrite: whenever a
    ``GroundedFact`` carries a ``display_name`` (see its own docstring),
    that exact name must appear within a short window of text around the
    matched value+unit, not just exist somewhere among the grounded
    facts. A fact with no ``display_name`` set keeps the old,
    name-independent check -- this only tightens markers this project
    already knows how to name, never a new class of false positive.
    """
    dates_in_text = set(_ISO_DATE_RE.findall(text))
    if allowed_dates is not None:
        ungrounded_dates = dates_in_text - allowed_dates
    else:
        ungrounded_dates = set()

    text_without_dates = _ISO_DATE_RE.sub(" ", text)

    allowed_values: set[float] = set()
    facts_by_value_unit: dict[tuple[float, str], list[GroundedFact]] = {}
    for fact in grounded_facts:
        allowed_values.update(fact.numeric_values)
        if fact.unit and fact.numeric_values:
            for value in fact.numeric_values:
                facts_by_value_unit.setdefault((value, fact.unit.strip().lower()), []).append(fact)

    ordinal_spans = {m.span(1) for m in _ORDINAL_LIST_MARKER_RE.finditer(text_without_dates)}

    ungrounded: list[str] = []
    consumed_spans: list[tuple[int, int]] = []

    for match in _VALUE_UNIT_RE.finditer(text_without_dates):
        raw_value, raw_unit = match.group(1), match.group(2)
        # The *full* match span (value + unit) is what must be excluded from
        # the bare-number scan below, not just the value's own span: a unit
        # like "mL/min/1.73m2" contains digits of its own ("1.73"), which
        # `_NUMBER_RE` would otherwise re-discover as a second, unrelated
        # "number" and reject as ungrounded -- a real regression an
        # independent review caught (a live eGFR answer like "91 mL/min/
        # 1.73m2" failed grounding solely because of the "1.73" inside the
        # unit string itself).
        consumed_spans.append(match.span())
        try:
            value = float(raw_value)
        except ValueError:
            continue

        candidates = facts_by_value_unit.get((value, raw_unit.strip().lower()), [])
        if not candidates:
            ungrounded.append(f"{raw_value}{raw_unit}")
            continue

        marker_names = {c.display_name for c in candidates if c.display_name}
        if marker_names:
            window = _sentence_context(text_without_dates, match.start(), match.end())
            if not any(re.search(rf"\b{re.escape(name)}\b", window, re.IGNORECASE) for name in marker_names):
                ungrounded.append(f"{raw_value}{raw_unit} (no matching marker name nearby)")

    for match in _NUMBER_RE.finditer(text_without_dates):
        span = match.span()
        if span in ordinal_spans or any(start <= span[0] and span[1] <= end for start, end in consumed_spans):
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
