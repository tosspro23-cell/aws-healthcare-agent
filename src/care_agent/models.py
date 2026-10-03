"""Typed data models shared across the agent pipeline.

These are plain dataclasses (no external dependency) so the whole package
runs with just the Python standard library. Every dataclass exposes
``as_dict`` for JSON-serializable trace/debug output.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Literal


def _as_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _as_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_as_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _as_dict(v) for k, v in obj.items()}
    return obj


class DictMixin:
    def as_dict(self) -> dict[str, Any]:
        return _as_dict(self)


@dataclass(frozen=True)
class Biomarker(DictMixin):
    concept_id: str
    display_name: str
    value: float
    unit: str
    classification: str | None = None
    classification_basis: str | None = None
    action_fields: tuple[str, ...] = field(default_factory=tuple)
    source: str | None = None


@dataclass(frozen=True)
class Panel(DictMixin):
    panel_id: str
    measurement_date: str  # ISO date, e.g. "2026-05-06"
    biomarkers: tuple[Biomarker, ...] = field(default_factory=tuple)
    overall_flags: tuple[str, ...] = field(default_factory=tuple)

    def get(self, concept_id: str) -> Biomarker | None:
        for b in self.biomarkers:
            if b.concept_id == concept_id:
                return b
        return None


@dataclass(frozen=True)
class Bloodwork(DictMixin):
    user_id: str
    latest_panel: Panel | None
    previous_panels: tuple[Panel, ...] = field(default_factory=tuple)

    def all_panels_newest_first(self) -> list[Panel]:
        panels = list(self.previous_panels)
        if self.latest_panel is not None:
            panels = [self.latest_panel, *panels]
        return sorted(panels, key=lambda p: p.measurement_date, reverse=True)


@dataclass(frozen=True)
class Medication(DictMixin):
    name: str
    source: str
    confidence: str


@dataclass(frozen=True)
class Allergy(DictMixin):
    name: str
    source: str
    confidence: str


@dataclass(frozen=True)
class UserProfile(DictMixin):
    user_id: str
    display_name: str
    age: int | None
    sex: str | None
    country: str | None
    height_cm: float | None = None
    weight_kg: float | None = None
    known_conditions: tuple[str, ...] = field(default_factory=tuple)
    medications: tuple[Medication, ...] = field(default_factory=tuple)
    allergies: tuple[Allergy, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class QuestionnaireFact(DictMixin):
    field: str
    value: str
    state: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class QuestionnaireCaution(DictMixin):
    kind: str
    detail: str
    source: str | None = None


@dataclass(frozen=True)
class QuestionnairePreference(DictMixin):
    field: str
    value: str
    source: str | None = None


@dataclass(frozen=True)
class QuestionnaireUnknown(DictMixin):
    field: str
    reason: str
    source: str | None = None


@dataclass(frozen=True)
class QuestionnaireDeclined(DictMixin):
    field: str
    instruction: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class QuestionnaireContext(DictMixin):
    user_id: str
    completed_at: str | None
    facts: tuple[QuestionnaireFact, ...] = field(default_factory=tuple)
    cautions: tuple[QuestionnaireCaution, ...] = field(default_factory=tuple)
    preferences: tuple[QuestionnairePreference, ...] = field(default_factory=tuple)
    unknowns: tuple[QuestionnaireUnknown, ...] = field(default_factory=tuple)
    declined: tuple[QuestionnaireDeclined, ...] = field(default_factory=tuple)
    style_hint: str | None = None

    def fact(self, field_name: str) -> QuestionnaireFact | None:
        for f in self.facts:
            if f.field == field_name:
                return f
        return None

    def has_caution_kind(self, kind: str) -> bool:
        return any(c.kind == kind for c in self.cautions)

    def caution(self, kind: str) -> QuestionnaireCaution | None:
        return next((c for c in self.cautions if c.kind == kind), None)


@dataclass(frozen=True)
class KnowledgeChunk(DictMixin):
    id: str
    title: str
    topic: tuple[str, ...]
    source_name: str
    source_url: str
    content: str


@dataclass(frozen=True)
class RetrievedChunk(DictMixin):
    chunk: KnowledgeChunk
    score: float
    matched_terms: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CatalogEntry(DictMixin):
    biomarker_name: str
    display_name: str
    unit: str
    direction: str | None
    domain_label: str | None
    importance: str | None
    interpretation_notes: str | None
    safety_notes: str | None
    action_fields: tuple[str, ...] = field(default_factory=tuple)
    optimal_range_min: float | None = None
    optimal_range_max: float | None = None
    adequate_range_min: float | None = None
    adequate_range_max: float | None = None


@dataclass(frozen=True)
class ToolCall(DictMixin):
    """A single record in the agent's execution trace."""

    name: str
    args: dict[str, Any]
    result_summary: str
    ok: bool = True
    # Wall-clock time this one step took, in milliseconds -- only populated
    # by `orchestrator.run_compound_reasoning` (V2's own tool-calling loop);
    # V1's fixed-pipeline trace entries leave this None. Purely additive, so
    # every existing call site (V1's own, and anything constructed before
    # this field existed) keeps working with no change.
    duration_ms: float | None = None


@dataclass(frozen=True)
class GroundedFact(DictMixin):
    """One atomic, source-attributed fact the final answer is allowed to state.

    Every number that appears in the composed answer must trace back to a
    ``GroundedFact`` with a matching numeric value (see ``safety.verify_numeric_grounding``).

    ``unit`` (e.g. ``"mg/dL"``, ``"%"``) is optional -- only biomarker-value
    facts carry one -- but when present it lets the safety check verify a
    number is grounded *for that specific unit*, not just present somewhere
    in the numeric_values across every fact. Without it, "Your HbA1c is
    162%" would pass grounding just because 162 happens to be a real,
    correctly-grounded LDL-C value in mg/dL -- the number alone doesn't
    prove it's attached to the right marker. Populated directly from the
    source biomarker's own `unit` field at construction time (see
    `agent.py`), never parsed back out of `claim`'s free text.

    ``display_name`` (e.g. ``"LDL-C"``) closes a narrower gap `unit` alone
    doesn't: several markers share the same unit (LDL-C/HDL-C/triglycerides/
    fasting glucose are all `mg/dL`), so a (value, unit) pair matching
    *some* fact doesn't prove the text attached it to the *right* marker --
    "Your LDL-C is 150 mg/dL" would still pass if 150 is only ever grounded
    as Triglycerides. When set, `safety.verify_numeric_grounding` requires
    this exact name to appear near the matched value+unit in the answer
    text, not just that the pair exists somewhere among the grounded facts.
    Optional and `None` for facts with no real biomarker name to check
    against (a panel-age fact, a questionnaire claim) -- those keep the
    older, name-independent check.

    ``display_name_aliases`` widens that same check to also accept a
    real, already-curated free-text alias of ``display_name`` (from
    `BiomarkerCatalog.aliases_for` -- the same `marker_alias` table
    `catalog.search_by_alias` already uses in the other direction), not
    just the exact catalog string. Found live: a real narrator wrote
    "LDL cholesterol" -- a genuine, already-vetted alias for "LDL-C", not
    a fabrication -- and was rejected only because the check required the
    literal display_name. Empty by default; populated at construction
    time wherever a fact's marker is known (see `agent.py`/
    `orchestrator.py`), never guessed inside `safety.py` itself.

    ``trend_previous_value``/``trend_previous_date``/``trend_latest_value``/
    ``trend_latest_date``/``trend_direction`` are populated only for a
    genuine two-point trend fact (see `orchestrator._build_trend_fact`) --
    `numeric_values` alone is an unordered bare tuple with no documented
    endpoint ordering and no direction, which let `safety.py` only ever
    verify a derived delta/percentage-change claim's *magnitude*, never
    whether it was being stated as a measurement vs. a change, or in the
    correct direction. An independent review found this exact gap live: a
    narrator's delta (the difference between two real values) was
    accepted as if it were itself a real measured value, and a correct
    magnitude stated with the *wrong* direction word passed too, since
    nothing recorded which endpoint was earlier/later or which way the
    real value actually moved. `None` for every fact that isn't a
    two-point trend -- which makes the derived-claim check in
    `safety.verify_numeric_grounding` inert for them, strictly more
    conservative than before, never less. See docs/DECISIONS.md.

    ``source_run_id`` is set only for a fact re-fetched from an *earlier*
    turn's own evidence (see `run_reads.fetch_prior_grounded_facts`) --
    `None` for a fact this turn's own tools just gathered. Exists so the
    trace can record which prior run a cross-turn grounding fact actually
    came from, instead of flattening multiple prior runs' facts into one
    list with no way to tell them apart later. See
    `AgentTrace.prior_evidence`'s own docstring.
    """

    claim: str
    source_type: Literal["bloodwork", "questionnaire", "knowledge_base", "catalog", "derived_policy"]
    source_ref: str
    numeric_values: tuple[float, ...] = field(default_factory=tuple)
    unit: str | None = None
    display_name: str | None = None
    display_name_aliases: tuple[str, ...] = field(default_factory=tuple)
    trend_previous_value: float | None = None
    trend_previous_date: str | None = None
    trend_latest_value: float | None = None
    trend_latest_date: str | None = None
    trend_direction: Literal["up", "down", "flat"] | None = None
    source_run_id: str | None = None


def grounded_fact_from_dict(d: dict[str, Any]) -> GroundedFact:
    """The missing reverse of `GroundedFact.as_dict()` -- reconstructs a
    real `GroundedFact` from the JSON shape a prior run's evidence was
    serialized as (`{run_id}.json` in S3, or any other `as_dict()`
    output). `numeric_values`/`display_name_aliases` round-trip through
    JSON as lists, not tuples; everything else passes through by field
    name unchanged."""
    return GroundedFact(
        claim=d["claim"],
        source_type=d["source_type"],
        source_ref=d["source_ref"],
        numeric_values=tuple(d.get("numeric_values") or ()),
        unit=d.get("unit"),
        display_name=d.get("display_name"),
        display_name_aliases=tuple(d.get("display_name_aliases") or ()),
        trend_previous_value=d.get("trend_previous_value"),
        trend_previous_date=d.get("trend_previous_date"),
        trend_latest_value=d.get("trend_latest_value"),
        trend_latest_date=d.get("trend_latest_date"),
        trend_direction=d.get("trend_direction"),
        source_run_id=d.get("source_run_id"),
    )


@dataclass(frozen=True)
class Limitation(DictMixin):
    kind: str
    detail: str


@dataclass(frozen=True)
class SafetyCheck(DictMixin):
    """One independent guardrail result (see ``care_agent.safety``).

    ``severity`` distinguishes two structurally different kinds of
    failure, for operators reviewing ``AgentTrace.safety_checks`` later --
    it does **not** change what reaches the patient: any failed check,
    hard or soft, still triggers the existing fallback-to-mock behavior
    in ``agent.py`` unchanged.

    - ``"hard"`` -- an unambiguous policy violation (diagnosis language,
      dosing instructions, an empty answer). There is no legitimate
      reading of a hard-check failure; it is always the narrator's fault.
    - ``"soft"`` -- ``numeric_grounding`` only. This file's own docstring
      documents a real, measured history of this specific check producing
      false positives (hyphenated compounds, unrecognized units, list
      markers) that were found and fixed one at a time. A soft failure is
      still rejected today, but it is the class of failure worth
      reviewing separately to see whether the check itself needs another
      fix, versus a hard failure, which never needs that kind of review.
    """

    name: str
    passed: bool
    detail: str = ""
    severity: Literal["hard", "soft"] = "hard"


@dataclass
class AgentTrace(DictMixin):
    question_id: str | None
    user_id: str
    intent: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)
    grounded_facts: list[GroundedFact] = field(default_factory=list)
    limitations: list[Limitation] = field(default_factory=list)
    safety_checks: list[SafetyCheck] = field(default_factory=list)
    narrator_backend: str = "mock"
    retriever_backend: str = "bm25"
    # Populated only when a non-mock narrator's draft failed a safety
    # check and the agent fell back to the deterministic narrator (see
    # `agent.py` and the "narrator_fallback" entry this produces in
    # `safety_checks`). This is the *rejected* draft, never the answer
    # actually returned -- kept for debugging/transparency (requested
    # directly after testing the Workbench: no way existed to see what an
    # LLM draft had said or why it was discarded), not shown to an end
    # user as advice.
    rejected_draft: str | None = None
    # Three-way classification of *why* this response looks the way it
    # does, set once in `agent.py` after the safety/fallback decision is
    # final -- an operator reviewing traces across many requests can
    # filter on this instead of re-deriving it from `safety_checks` every
    # time. This does **not** change what was returned to the patient;
    # the fallback behavior it records already happened before this field
    # is set.
    #
    # - "answered": every check passed on the first draft; no fallback.
    # - "answered_after_soft_fallback": the rejected draft failed only
    #   `numeric_grounding` (severity "soft") -- worth reviewing whether
    #   the check itself has another false-positive gap, same as the
    #   real ones this project has found and fixed before.
    # - "answered_after_hard_fallback": the rejected draft failed at
    #   least one "hard" check (diagnosis, dosing, empty answer) -- an
    #   unambiguous narrator failure, never the check's fault.
    disposition: Literal["answered", "answered_after_soft_fallback", "answered_after_hard_fallback"] = "answered"
    # Ground-truth wall-clock time for the whole `ask()`/`ask_compound()`
    # call, in milliseconds -- set once, right before returning, in
    # `_narrate_and_verify` (the single tail-call every return path goes
    # through). Independent of summing individual `ToolCall.duration_ms`
    # entries, which is useful precisely because those don't (and aren't
    # meant to) account for every microsecond of overhead between them.
    total_duration_ms: float | None = None
    # Facts re-fetched from an *earlier* turn in the same conversation
    # (see `run_reads.fetch_prior_grounded_facts`) that widened THIS
    # turn's own `numeric_grounding` check -- kept entirely separate from
    # `grounded_facts` (which stays exactly "what this turn's own tools
    # gathered") so the Evidence panel never conflates the two, but
    # recorded here so the trace stays self-sufficient: an independent
    # review found that without this, a trace alone couldn't be replayed
    # later to reproduce why a draft referencing an older number actually
    # passed -- that number's only grounding evidence lived in memory for
    # the one request that used it and was never written down anywhere.
    # Each fact's own `source_run_id` records which prior run it came
    # from. See docs/DECISIONS.md.
    prior_evidence: list[GroundedFact] = field(default_factory=list)


@dataclass
class AgentResponse(DictMixin):
    answer: str
    trace: AgentTrace
    safe: bool
