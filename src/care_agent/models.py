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
    """

    claim: str
    source_type: Literal["bloodwork", "questionnaire", "knowledge_base", "catalog", "derived_policy"]
    source_ref: str
    numeric_values: tuple[float, ...] = field(default_factory=tuple)
    unit: str | None = None
    display_name: str | None = None
    display_name_aliases: tuple[str, ...] = field(default_factory=tuple)


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


@dataclass
class AgentResponse(DictMixin):
    answer: str
    trace: AgentTrace
    safe: bool
