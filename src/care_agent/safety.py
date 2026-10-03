"""Safety guardrails applied to every composed answer before it is returned.

Four independent checks, run in order. Each carries a ``severity`` (see
``SafetyCheck.severity``): checks 1-3 are ``"hard"`` (unambiguous policy
violations -- never the check's own fault), check 4 is ``"soft"`` (this
file's own documented history of real false positives in this specific
check makes it worth reviewing separately). This classification is for
`AgentTrace.disposition`, set in `agent.py` -- it does not change the
fallback behavior below, which still triggers on *any* failed check,
hard or soft alike.

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
name" to begin with), still only gets the weaker value-only check.
``_NUMBER_RE`` also won't flag a number immediately glued to a preceding
word by a hyphen ("medication-500mg") -- added deliberately to stop
flagging hyphenated English compounds ("omega-3", "COVID-19") as fake
ungrounded numbers, a real, measured cost found live (see
``_NUMBER_RE``'s own comment and ``docs/DECISIONS.md``), at the accepted
cost of this one narrow, contrived phrasing no real narrator output has
ever produced. None of this is a substitute for ``agent.py``'s existing
fallback-to-mock-narrator behavior on any check failure, which remains
the actual safety net.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from care_agent.models import GroundedFact, SafetyCheck

_CONDITIONS = (
    r"(?:type\s?[12]\s?diabetes|diabetes|prediabetes|(?:heart|cardiovascular) disease|"
    r"(?:kidney|renal) disease|(?:liver|hepatic) disease|insulin resistance|metabolic syndrome)"
)


# The clinician persona's own system prompt (see narrator/_prompt.py)
# explicitly instructs the model to write "the patient", never "you" --
# an independent review found every pattern below was written assuming
# second-person phrasing, so a clinician-persona answer restating the
# exact same forbidden claim in third person ("The patient has
# diabetes.") passed every check outright. Every subject-specific
# pattern now matches both forms via an explicit alternation, not just
# "you"/"your" -- these are not two independently-maintained pattern
# lists, since a future third phrasing (e.g. a different persona) would
# reopen the same gap otherwise. See docs/DECISIONS.md, 2026-09-21 entry.
_DIAGNOSIS_PATTERNS = [
    rf"\b(?:you (?:have|are)|the patient (?:has|is)) (a |an )?{_CONDITIONS}\b",
    r"\b(?:you are|the patient is) (pre)?diabetic\b",
    r"\b(?:you(?:'re| are)|the patient is) diagnosed with\b",
    r"\b(?:you(?:'ve| have)|the patient has) been diagnosed with\b",
    r"\bthis (means|confirms) (?:you have|the patient has)\b",
    r"\b(?:your|the patient's) diagnosis is\b",
    r"\b(?:your|the patient's) condition is\b",
    rf"\b{_CONDITIONS} is (?:your|the patient's) (confirmed |diagnosed )?condition\b",
    rf"\b(?:your|the patient's) (confirmed |diagnosed )?condition is {_CONDITIONS}\b",
]

_DOSAGE_FORMS = r"(?:capsule|tablet|pill|softgel|gummy|dose)"
_FREQUENCY_WORDS = r"(?:every morning|every evening|every night|each morning|each evening|daily|once a day|twice a day|three times a day)"

_DOSING_PATTERNS = [
    r"\btake \d+\s?(mg|mcg|iu|g|ml)\b",
    r"\b\d+\s?(mg|mcg|iu|g|ml)\s?(per day|daily|/day|a day|twice|once)\b",
    r"\bstart taking\b",
    r"\bstop taking\b",
    # See _DIAGNOSIS_PATTERNS's comment above -- same third-person gap,
    # same fix: "the patient's dose"/"the patient's medication" instead
    # of only "your dose"/"your medication".
    r"\bincrease (?:your|the patient's) dose\b",
    r"\bdecrease (?:your|the patient's) dose\b",
    r"\bswitch (?:your |the patient's )?medication\b",
    r"\bchange (?:your|the patient's) (dose|dosage|medication)\b",
    rf"\b(swallow|take) (one|two|three|a|an) {_DOSAGE_FORMS}\b",
    rf"\b{_DOSAGE_FORMS}\b.{{0,40}}\b{_FREQUENCY_WORDS}\b",
    rf"\b{_FREQUENCY_WORDS}\b.{{0,40}}\b{_DOSAGE_FORMS}\b",
]

# The real, complete unit vocabulary this project's sample data actually
# uses (verified against every marker in data/sample_bloodwork.json, not
# guessed) -- adding a new marker type with a different unit requires
# adding it here too, or numbers attached to that unit only get the
# weaker value-only check below. "percentage points"/"percentage point"
# are not a data unit but a real, common way to phrase a flat delta
# between two "%" values ("HbA1c rose by 0.3 percentage points") -- a
# live rejected draft found this exact phrasing didn't match the tight
# `_VALUE_UNIT_RE` at all (no data unit sits immediately after the
# number), so it fell through to the weak bare-number path, which never
# learned about computed deltas in the first place. Normalized to "%"
# before the lookup below, not treated as a separate unit bucket.
_KNOWN_UNITS = ("mg/dL", "mg/L", "ng/mL", "mIU/L", "mL/min/1.73m2", "U/L", "percentage points", "percentage point", "%")
_UNIT_ALIASES = {"percentage points": "%", "percentage point": "%"}
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
# check entirely, since a following letter blocked the match. First
# lookbehind: still won't match the "006" inside an identifier like
# "kb_a1c_006", since "_" is a word character.
#
# Second lookbehind added after a real live failure, not found by
# inspection: a hyphenated English compound -- "omega-3", "COVID-19",
# "type-2", "stage-4" -- has its trailing digit(s) extracted as if they
# were a standalone ungrounded number, since the character immediately
# before the digit is a hyphen (not `\w`/`.`), even though the hyphen
# itself is correctly excluded from starting a match (the first
# lookbehind already blocks that, since a letter precedes the hyphen).
# Reproduced live: asking the real Bedrock narrator "what foods should I
# eat" with reference material about fish (see docs/DECISIONS.md) made it
# write "omega-3 fatty acids" often enough to fail numeric_grounding on
# "3" in 3 of 6 real runs -- a real, measured cost, not a hypothetical
# one. `(?<![A-Za-z]-)` excludes a match starting right after a
# letter-then-hyphen, fixing exactly that class, while deliberately NOT
# excluding a match after a *digit*-then-hyphen: "aim for 5-10 servings"
# must still flag both 5 and 10 (a real, if separate, source of
# unnecessary fallback for generic-guideline numbers -- accepted as
# consistent with this check's own conservative "every standalone number
# needs grounding" design, not a bug this fix should also paper over).
#
# **Known residual gap, not closed by this fix**: a hyphen immediately
# after a letter also hides a *deliberately* hyphen-glued number from
# this specific check -- "medication-500mg" no longer flags "500" the
# way "500 mg" would. This is a narrow, contrived phrasing no observed
# narrator output has ever produced (real dosing language reads "take
# 500 mg", not "medication-500mg"), and `check_no_dosing`'s own
# independent pattern list is a separate defense layer for realistic
# dosing phrasing regardless of this check -- but it is a real, accepted
# limit of this specific regex, consistent with this file's own "Honest
# limits, not a claim of completeness" section above, not a claim that
# this closes every hyphen-based bypass.
_NUMBER_RE = re.compile(r"(?<![\w.])(?<![A-Za-z]-)-?\d+\.?\d*")
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

# Vocabulary a narrator might use to state a trend's direction in prose --
# mirrors mock_narrator.py's own `_DIRECTION_PHRASE` ("increased"/
# "decreased"/"stayed about the same"), widened with synonyms a real LLM
# narrator plausibly reaches for when instructed (see narrator/_prompt.py's
# shared rules) to state "an increase" or "a percentage change" in its own
# words. Used only to verify a *derived* (delta/percentage-change) claim's
# stated direction against the fact's real one -- an independent review
# found a magnitude-correct but direction-reversed claim ("decreased by
# 14 mg/dL" for a marker that actually rose) previously passed outright,
# since nothing checked which way the real value moved. See
# `GroundedFact.trend_direction`'s own docstring and docs/DECISIONS.md.
_DIRECTION_WORDS: dict[str, Literal["up", "down", "flat"]] = {
    "increase": "up",
    "increased": "up",
    "increasing": "up",
    "rise": "up",
    "rises": "up",
    "rising": "up",
    "rose": "up",
    "risen": "up",
    "higher": "up",
    "up": "up",
    "climbed": "up",
    "climbing": "up",
    "gained": "up",
    "jumped": "up",
    "grew": "up",
    "decrease": "down",
    "decreased": "down",
    "decreasing": "down",
    "fall": "down",
    "falls": "down",
    "falling": "down",
    "fell": "down",
    "fallen": "down",
    "lower": "down",
    "down": "down",
    "dropped": "down",
    "dropping": "down",
    "declined": "down",
    "declining": "down",
    "reduced": "down",
    "unchanged": "flat",
    "stable": "flat",
    "steady": "flat",
    "flat": "flat",
}


def _direction_nearby(window: str) -> Literal["up", "down", "flat"] | None:
    """The first recognized direction word found in `window`, mapped to
    up/down/flat -- or `None` if no direction word appears at all. Only
    used to verify a *derived* (delta/percentage) claim's stated
    direction; a literal measurement claim never needs this."""
    for word, direction in _DIRECTION_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", window, re.IGNORECASE):
            return direction
    return None


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

# How many sentences the numeric-grounding anaphora fallback (see
# `verify_numeric_grounding`) will widen backward looking for a marker
# name established earlier and only referred to by pronoun since. 4
# comfortably covers the real chained-pronoun draft that motivated
# raising this from 2 (see docs/DECISIONS.md) with a sentence of margin,
# while still stopping well short of an unbounded scan.
_MAX_ANAPHORA_SENTENCES_BACK = 4


def _is_sentence_boundary(text: str, i: int) -> bool:
    """`text[i] in _SENTENCE_BOUNDARY_CHARS`, except a "." flanked by
    digits on both sides (a decimal point, e.g. the "." in "5.8") never
    counts -- found live, not by inspection: `orchestrator.py`'s V2 path
    can render two decimal-valued facts for the same marker in one
    sentence/line (a trend fact: "5.8 % on ... -> 6.1 % on ..."), and the
    *first* number's own decimal point was being read as a sentence
    boundary, truncating the *second* number's marker-name-proximity
    window (`verify_numeric_grounding`'s cross-marker check) before it
    ever reached the marker name earlier in the same sentence -- a false
    "no matching marker name nearby" grounding failure on a genuinely
    grounded value. See docs/DECISIONS.md, 2026-09-20 entry."""
    ch = text[i]
    if ch not in _SENTENCE_BOUNDARY_CHARS:
        return False
    if ch == "." and i > 0 and text[i - 1].isdigit() and i + 1 < len(text) and text[i + 1].isdigit():
        return False
    return True


def _sentence_context(text: str, start: int, end: int, sentences_back: int = 1) -> str:
    """The current sentence/line around `text[start:end]`: expands outward
    to the nearest preceding and following sentence-ending punctuation or
    newline, capped at `_CONTEXT_MAX_CHARS` in each direction.

    `sentences_back` (default 1, i.e. just the current sentence) can widen
    the *left* boundary to cross that many sentence breaks instead of one
    -- used by `verify_numeric_grounding`'s anaphora fallback (see its own
    comment) to recover a value whose marker name was established one
    sentence earlier and only referred to by pronoun in the value's own
    sentence, without touching the *forward* boundary at all."""
    left_cap = max(0, start - _CONTEXT_MAX_CHARS)
    # `left` only ever gets set once the `sentences_back`-th boundary is
    # actually found -- not on every intermediate boundary crossed while
    # still under budget. Setting it eagerly on each one (a bug an actual
    # live test of `sentences_back=2` caught: it silently behaved
    # identically to `sentences_back=1` whenever only one boundary existed
    # in the scanned range at all) would stop `left` at the *first*
    # boundary found even when fewer boundaries exist than requested,
    # instead of correctly falling through to `left_cap` (the full
    # capped range, including the sentence before the first one) in that
    # case.
    left = left_cap
    boundaries_crossed = 0
    for i in range(start - 1, left_cap - 1, -1):
        if _is_sentence_boundary(text, i):
            boundaries_crossed += 1
            if boundaries_crossed >= sentences_back:
                left = i + 1
                break

    right_cap = min(len(text), end + _CONTEXT_MAX_CHARS)
    right = right_cap
    for i in range(end, right_cap):
        if _is_sentence_boundary(text, i):
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

    @property
    def has_hard_failure(self) -> bool:
        """Any failed check with severity "hard" -- an unambiguous policy
        violation, never the check's own fault. See `SafetyCheck.severity`."""
        return any(c.severity == "hard" for c in self.failed_checks)


def check_non_empty(text: str) -> SafetyCheck:
    if not text or not text.strip():
        return SafetyCheck(name="non_empty", passed=False, detail="Answer text is empty or whitespace-only.", severity="hard")
    return SafetyCheck(name="non_empty", passed=True, severity="hard")


def check_no_diagnosis(text: str) -> SafetyCheck:
    lowered = text.lower()
    for pattern in _DIAGNOSIS_PATTERNS:
        if re.search(pattern, lowered):
            return SafetyCheck(name="no_diagnosis", passed=False, detail=f"Matched forbidden pattern: {pattern!r}", severity="hard")
    return SafetyCheck(name="no_diagnosis", passed=True, severity="hard")


def check_no_dosing(text: str) -> SafetyCheck:
    lowered = text.lower()
    for pattern in _DOSING_PATTERNS:
        if re.search(pattern, lowered):
            return SafetyCheck(name="no_dosing", passed=False, detail=f"Matched forbidden pattern: {pattern!r}", severity="hard")
    return SafetyCheck(name="no_dosing", passed=True, severity="hard")


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

    **Derived claims (delta/percentage change)**: a trend fact's absolute
    delta and percentage change (computed from ``trend_previous_value``/
    ``trend_latest_value``, never from a bare, unordered ``numeric_values``
    tuple) are matched *separately* from literal measurements, and only
    when a recognized direction word (see ``_DIRECTION_WORDS``) appears
    nearby whose direction matches the fact's real ``trend_direction``
    exactly. An independent review found two real gaps this closes: a
    narrator's delta ("Your latest LDL-C is 14 mg/dL.", 14 being
    162-148) was previously accepted as if 14 were itself a measured
    value, since the old code folded deltas into the *same* lookup real
    measurements use; and a magnitude-correct claim stated with the
    *wrong* direction ("decreased by 14 mg/dL" for a marker that actually
    rose) also passed, since nothing verified which way the value moved,
    and the old percentage check tried *both* possible baselines rather
    than requiring the one real one (``trend_previous_value``). A fact
    with no ``trend_direction`` set (anything that isn't a genuine
    two-point trend fact) never matches a derived claim at all -- no
    regression risk for any other kind of grounded fact. See
    `GroundedFact`'s own docstring and docs/DECISIONS.md.
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

    # Derived (delta/percentage-change) claims are matched *separately*
    # from literal measurements above, never folded into the same lookup
    # -- see this function's own docstring for the two real false-accepts
    # this separation (plus the direction check below) closes. Only a
    # genuine two-point trend fact (`trend_direction` set -- see
    # `GroundedFact`'s own docstring) ever contributes an entry here;
    # anything else simply can't match a derived claim at all.
    derived_claims_by_value_unit: dict[tuple[float, str], list[GroundedFact]] = {}
    for fact in grounded_facts:
        if fact.trend_direction is None or fact.trend_previous_value is None or fact.trend_latest_value is None or not fact.unit:
            continue
        unit_key = fact.unit.strip().lower()
        # Rounded to 6 decimal places -- found live, not by inspection:
        # `6.1 - 5.8` is `0.2999999999999998` in IEEE 754 floats, not
        # exactly `0.3`, so a real narrator writing the entirely correct
        # "0.3%" was rejected outright because the unrounded subtraction
        # result didn't `==` the clean `float("0.3")` parsed from that
        # text, despite both representing the same real-world number. No
        # real lab value or narrated delta in this project needs more
        # than a couple decimal places, so 6 is a wide margin, not a
        # precision compromise. See docs/DECISIONS.md.
        delta = round(abs(fact.trend_latest_value - fact.trend_previous_value), 6)
        derived_claims_by_value_unit.setdefault((delta, unit_key), []).append(fact)
        # Percentage change is a *different* derived unit ("%") from the
        # fact's own, and needs a baseline to divide by -- always
        # `trend_previous_value`, the one real baseline, never both
        # possible orderings the way a bare, unordered `numeric_values`
        # tuple used to force this check to guess. Rounded to whole and
        # one-decimal percent -- the two precisions a narrator is
        # actually likely to write ("9%" or "9.5%" for a true 9.46%), not
        # left unrounded only, which would reject any real narration that
        # rounds at all.
        if fact.trend_previous_value != 0:
            pct_change = delta / abs(fact.trend_previous_value) * 100
            for rounded in (round(pct_change), round(pct_change, 1)):
                derived_claims_by_value_unit.setdefault((float(rounded), "%"), []).append(fact)

    # Every marker name this *answer* could legitimately be talking about
    # -- not just the ones relevant to the value currently being checked.
    # Used only to distinguish "this sentence names no marker at all"
    # (a pronoun/implicit reference -- see the anaphora fallback below)
    # from "this sentence names a *different* marker" (a genuine mismatch
    # that must still be rejected, not widened past).
    all_marker_names: set[str] = set()
    for fact in grounded_facts:
        if fact.display_name:
            all_marker_names.add(fact.display_name)
        all_marker_names.update(fact.display_name_aliases)

    ordinal_spans = {m.span(1) for m in _ORDINAL_LIST_MARKER_RE.finditer(text_without_dates)}

    def _names_nearby(names: set[str], window: str) -> bool:
        return any(re.search(rf"\b{re.escape(name)}\b", window, re.IGNORECASE) for name in names)

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

        unit_key = raw_unit.strip().lower()
        unit_key = _UNIT_ALIASES.get(unit_key, unit_key)
        candidates = facts_by_value_unit.get((value, unit_key), [])
        is_derived_claim = False
        if not candidates:
            candidates = derived_claims_by_value_unit.get((value, unit_key), [])
            is_derived_claim = True
        if not candidates:
            ungrounded.append(f"{raw_value}{raw_unit}")
            continue

        window = _sentence_context(text_without_dates, match.start(), match.end())

        if is_derived_claim:
            # A derived claim must also state the correct direction --
            # the magnitude alone isn't evidence enough, since the exact
            # same magnitude reads as "correct" phrased as either an
            # increase or a decrease; only one of those is the real
            # claim. No recognized direction word in this sentence, or
            # one that doesn't match the fact's real direction, means
            # this can't be verified and must be rejected. See this
            # function's own docstring.
            direction = _direction_nearby(window)
            matching_direction_facts = [c for c in candidates if c.trend_direction == direction]
            if direction is None or not matching_direction_facts:
                ungrounded.append(f"{raw_value}{raw_unit} (derived claim with no matching direction nearby)")
                continue
            candidates = matching_direction_facts

        # Any of the marker's *curated* names -- the exact catalog
        # display_name, or a real, already-vetted alias of it (e.g. "LDL
        # cholesterol" for "LDL-C") -- counts. Widening this to arbitrary
        # fuzzy matching would reopen the exact "real number, wrong
        # marker" bypass this check exists to close; widening it to a
        # *curated* alias list this project already maintains for the
        # same marker (see `GroundedFact.display_name_aliases`'s own
        # docstring) does not, since every accepted name still resolves
        # to this one specific concept_id and no other.
        marker_names = {c.display_name for c in candidates if c.display_name}
        marker_names |= {alias for c in candidates for alias in c.display_name_aliases}
        if marker_names:
            found = _names_nearby(marker_names, window)
            if not found and not _names_nearby(all_marker_names, window):
                # The current sentence names no marker at all -- likely an
                # implicit/pronoun reference to a marker established one or
                # more sentences earlier ("Your LDL-C has increased... On
                # <date>, it was 148 mg/dL... it had risen to 162 mg/dL..."),
                # a real false positive a production draft rejected live
                # (see docs/DECISIONS.md) -- a *chain* of pronoun-only
                # sentences, not just one, so a single extra sentence of
                # widening (the original version of this fix) wasn't
                # enough. Keep widening one sentence at a time, but stop
                # the instant any sentence in the (growing) window names a
                # marker that isn't the target one -- a genuine mismatch
                # must still be rejected without widening further past it,
                # exactly as before. `_MAX_ANAPHORA_SENTENCES_BACK` keeps
                # this bounded, not an unbounded backward scan (further
                # capped in absolute distance by `_CONTEXT_MAX_CHARS`
                # regardless of sentence count).
                for sentences_back in range(2, _MAX_ANAPHORA_SENTENCES_BACK + 1):
                    wider_window = _sentence_context(text_without_dates, match.start(), match.end(), sentences_back=sentences_back)
                    found = _names_nearby(marker_names, wider_window)
                    if found or _names_nearby(all_marker_names, wider_window):
                        break
            if not found:
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
        return SafetyCheck(name="numeric_grounding", passed=False, detail="; ".join(problems), severity="soft")
    return SafetyCheck(name="numeric_grounding", passed=True, severity="soft")


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
