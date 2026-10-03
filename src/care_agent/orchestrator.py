"""Tool-calling compound-reasoning engine -- V2, additive to `agent.py`'s
existing fixed 5-intent pipeline (V1), never replacing it.

Architecture (see `docs/DECISIONS.md`, 2026-09-20 entry, for the full
design discussion this implements):

- A `ToolPlanner` decides *which* of this module's fixed, deterministic
  tools answer a question, and with what arguments -- it never computes a
  clinical fact itself. This mirrors the pattern independently verified
  in this project's Construction Intelligence sibling (SPEC-M16): the
  model's only job is choosing tool calls; every tool wraps the *same*
  deterministic Python this project's V1 pipeline already uses
  (`reasoning.py`, `trend.py`) -- no new computation logic exists here.
- Every planned call is checked by `capability_gate` before it runs --
  an unrecognized tool name or an out-of-vocabulary `concept_id` is
  rejected, never silently guessed at or invented.
- `run_compound_reasoning` bounds itself to `MAX_ITERATIONS` rounds (an
  initial plan, plus one bounded repair attempt if every call in it was
  rejected) -- never an unbounded loop.
- The output is a fully-populated `Brief` -- the *same* type V1's fixed
  pipeline produces. This is deliberate: it means `agent.py`'s
  `_narrate_and_verify` (narrate, run `safety.run_safety_checks`, fall
  back to the mock narrator on any failure) applies completely unchanged
  to a V2-built `Brief`. There is exactly one safety gate in this
  project, not two -- V2 does not get a separate, weaker one.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast

from care_agent.catalog import BiomarkerCatalog
from care_agent.intent import COMPOUND_REASONING
from care_agent.models import Bloodwork, GroundedFact, Limitation, QuestionnaireContext, ToolCall, UserProfile
from care_agent.plausibility import SUPPORTED_CONCEPT_IDS
from care_agent.reasoning import CONCEPT_TOPIC_TAGS, Brief, FocusItem, build_supplement_cautions, rank_focus_markers
from care_agent.retrieval.base import Retriever
from care_agent.trend import TrendResult, compute_trend

MAX_ITERATIONS = 2  # initial plan + one bounded repair attempt; see module docstring


@dataclass(frozen=True)
class PlannedToolCall:
    tool_name: str
    args: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolPlan:
    calls: tuple[PlannedToolCall, ...]
    rationale: str = ""


# --------------------------------------------------------------------------
# Tool schema (also the JSON-schema payload a real provider's tool-use API,
# e.g. Bedrock Converse's `toolConfig`, is given -- see `BedrockToolPlanner`)
# --------------------------------------------------------------------------

TOOL_SPECS: list[dict] = [
    {
        "name": "get_marker_trend",
        "description": (
            "Get whether a specific biomarker is trending up, down, or flat across the user's panels. "
            "Use for any question comparing a marker over time."
        ),
        "parameters": {"concept_id": {"type": "string", "enum": list(SUPPORTED_CONCEPT_IDS), "description": "The biomarker's concept_id."}},
    },
    {
        "name": "get_marker_snapshot",
        "description": "Get a specific biomarker's latest value, unit, and classification (e.g. 'high', 'normal').",
        "parameters": {"concept_id": {"type": "string", "enum": list(SUPPORTED_CONCEPT_IDS), "description": "The biomarker's concept_id."}},
    },
    {
        "name": "compare_marker_trends",
        "description": (
            "Check whether two different biomarkers are trending in the same direction, opposite directions, "
            "or an inconsistent pattern, over the same measurement history. Use for any question relating one "
            "marker's change to another's (e.g. comparing LDL and A1C trends together)."
        ),
        "parameters": {
            "concept_id_a": {"type": "string", "enum": list(SUPPORTED_CONCEPT_IDS), "description": "The first biomarker's concept_id."},
            "concept_id_b": {"type": "string", "enum": list(SUPPORTED_CONCEPT_IDS), "description": "The second biomarker's concept_id."},
        },
    },
    {
        "name": "get_focus_markers",
        "description": "Get every biomarker in the latest panel the dataset already classifies as abnormal, ranked by severity.",
        "parameters": {},
    },
    {
        "name": "get_questionnaire_fact",
        "description": (
            "Look up one field the user reported in their intake questionnaire (e.g. 'nutrition.sugary_foods', "
            "'exercise.aerobic_activity', 'mind.sleep_duration', 'mind.stress'). Returns a not-reported result "
            "if that field wasn't answered -- never invents an answer."
        ),
        "parameters": {"field": {"type": "string", "description": "The questionnaire field name."}},
    },
    {
        "name": "get_supplement_cautions",
        "description": "Get this user's reported medication/allergy cautions relevant to supplement safety.",
        "parameters": {},
    },
    {
        "name": "get_allergies",
        "description": "Get this user's reported allergy list.",
        "parameters": {},
    },
    {
        "name": "search_knowledge",
        "description": (
            "Search the vetted knowledge base for general educational background on a topic (not a source of patient-specific facts)."
        ),
        "parameters": {"query": {"type": "string", "description": "Search query."}},
    },
]

_TOOL_NAMES = {spec["name"] for spec in TOOL_SPECS}
# Every declared parameter name per tool, derived from `TOOL_SPECS` rather
# than hand-listed a second time -- see `capability_gate`'s own docstring
# for why this matters.
_TOOL_PARAMS: dict[str, tuple[str, ...]] = {spec["name"]: tuple(spec["parameters"]) for spec in TOOL_SPECS}


def capability_gate(call: PlannedToolCall) -> tuple[bool, str | None]:
    """Reject a planned call before it ever executes. Every rejection
    reason is concrete enough to feed back into a repair prompt (see
    `run_compound_reasoning`).

    Every parameter `TOOL_SPECS` declares for a tool is required here to
    be present and a non-empty string -- generic over `TOOL_SPECS`, not
    one hand-picked check per tool. An independent review found
    `get_questionnaire_fact({})` (missing its required `field` argument)
    passed this gate cleanly and then crashed `execute_tool_call` with a
    bare `KeyError`, entirely bypassing the planner's own repair path --
    the two bespoke checks that existed before (`concept_id`, `query`)
    happened to cover every *other* tool's single required argument, so
    this exact gap was invisible until a tool without one of those two
    specific names was tried. See docs/DECISIONS.md, 2026-09-21 entry."""
    if call.tool_name not in _TOOL_NAMES:
        return False, f"Unknown tool {call.tool_name!r}. Supported tools: {sorted(_TOOL_NAMES)}."
    for param_name in _TOOL_PARAMS[call.tool_name]:
        value = call.args.get(param_name)
        if not isinstance(value, str) or not value:
            return False, f"{call.tool_name!r} requires a non-empty string argument {param_name!r}, got {value!r}."
    if call.tool_name in {"get_marker_trend", "get_marker_snapshot"}:
        concept_id = call.args["concept_id"]
        if concept_id not in SUPPORTED_CONCEPT_IDS:
            return False, f"Unsupported concept_id {concept_id!r} for {call.tool_name!r}. Supported: {sorted(SUPPORTED_CONCEPT_IDS)}."
    if call.tool_name == "compare_marker_trends":
        concept_id_a, concept_id_b = call.args["concept_id_a"], call.args["concept_id_b"]
        for concept_id in (concept_id_a, concept_id_b):
            if concept_id not in SUPPORTED_CONCEPT_IDS:
                return (
                    False,
                    f"Unsupported concept_id {concept_id!r} for 'compare_marker_trends'. Supported: {sorted(SUPPORTED_CONCEPT_IDS)}.",
                )
        # A self-comparison is never a legitimate request -- get_marker_trend
        # already exists for a single marker, and "is X trending the same
        # direction as X" is a degenerate question, not a real one.
        if concept_id_a == concept_id_b:
            return False, f"'compare_marker_trends' requires two different concept_ids, got {concept_id_a!r} twice."
    return True, None


# --------------------------------------------------------------------------
# Execution context + tool dispatch
# --------------------------------------------------------------------------


@dataclass
class ToolExecutionContext:
    """Everything a tool needs to run, bundled once per `ask_compound` call
    -- the same data `HealthAgent.ask()` already loads via `DataStore`."""

    profile: UserProfile
    bloodwork: Bloodwork
    questionnaire: QuestionnaireContext
    catalog: BiomarkerCatalog
    retriever: Retriever


@dataclass
class ToolExecutionResult:
    call: PlannedToolCall
    ok: bool
    result_summary: str
    grounded_facts: list[GroundedFact] = field(default_factory=list)
    limitations: list[Limitation] = field(default_factory=list)
    focus_items: list[FocusItem] = field(default_factory=list)
    mentioned_markers: dict = field(default_factory=dict)
    retrieved_chunks: list = field(default_factory=list)


def _marker_display_name(ctx: ToolExecutionContext, concept_id: str) -> str:
    entry = ctx.catalog.lookup(concept_id)
    return entry.display_name if entry else concept_id


def _build_trend_fact(ctx: ToolExecutionContext, concept_id: str, trend: TrendResult) -> GroundedFact:
    """Shared by `_execute_get_marker_trend` and `_execute_compare_marker_trends`
    -- both ground the *same* two numbers the same way, so the claim-
    formatting logic exists exactly once. `trend.available` must already be
    True; callers each handle the unavailable case themselves since the
    result_summary/Limitation wording differs slightly between a single-
    marker lookup and a two-marker comparison."""
    display_name = _marker_display_name(ctx, concept_id)
    assert trend.latest_value is not None and trend.previous_value is not None  # guaranteed by TrendResult when available=True
    assert trend.direction in ("up", "down", "flat")  # same guarantee
    return GroundedFact(
        claim=(
            f"{display_name} trend: {trend.previous_value} {trend.unit} on {trend.previous_date} -> "
            f"{trend.latest_value} {trend.unit} on {trend.latest_date} ({trend.direction})"
        ),
        source_type="bloodwork",
        source_ref=f"trend:{concept_id}",
        numeric_values=(float(trend.latest_value), float(trend.previous_value)),
        unit=trend.unit,
        display_name=display_name,
        display_name_aliases=ctx.catalog.aliases_for(concept_id),
        # Explicit endpoints/direction -- closes a real safety gap found
        # live: `numeric_values` alone is an unordered bare tuple, so
        # `safety.py` had no way to verify a narrated delta/percentage
        # claim's direction, or to stop a delta being accepted as if it
        # were itself a literal measurement. See GroundedFact's own
        # docstring and docs/DECISIONS.md.
        trend_previous_value=float(trend.previous_value),
        trend_previous_date=trend.previous_date,
        trend_latest_value=float(trend.latest_value),
        trend_latest_date=trend.latest_date,
        trend_direction=cast(Literal["up", "down", "flat"], trend.direction),
    )


def _execute_get_marker_trend(ctx: ToolExecutionContext, concept_id: str) -> ToolExecutionResult:
    trend = compute_trend(ctx.bloodwork, concept_id)
    display_name = _marker_display_name(ctx, concept_id)
    if not trend.available:
        return ToolExecutionResult(
            call=PlannedToolCall("get_marker_trend", {"concept_id": concept_id}),
            ok=True,
            result_summary=trend.reason_unavailable or "trend unavailable",
            limitations=[Limitation(kind="trend_unavailable", detail=f"{display_name}: {trend.reason_unavailable}")],
        )
    fact = _build_trend_fact(ctx, concept_id, trend)
    return ToolExecutionResult(
        call=PlannedToolCall("get_marker_trend", {"concept_id": concept_id}),
        ok=True,
        result_summary=f"{display_name} {trend.direction}: {trend.previous_value}->{trend.latest_value} {trend.unit}",
        grounded_facts=[fact],
    )


# Relationship categories a two-marker direction comparison can fall into --
# deliberately just three buckets, each described in plain language with no
# new numbers of its own (the two underlying trend facts already carry every
# number this claim could reference). "Flat" on either side goes to the
# inconsistent bucket rather than being treated as a direction match/mismatch
# -- "flat" isn't really "the same direction" as "up", and claiming otherwise
# would overstate what the data actually shows.
def _compare_directions(direction_a: str, direction_b: str) -> str:
    if direction_a == "flat" or direction_b == "flat":
        return "inconsistent"
    if direction_a == direction_b:
        return "co-moving"
    return "opposite"


_RELATIONSHIP_CLAIM = {
    "co-moving": "{a} and {b} are both trending {dir_a} over their tracked measurement periods.",
    "opposite": "{a} and {b} are trending in opposite directions ({a} {dir_a}, {b} {dir_b}) over their tracked measurement periods.",
    "inconsistent": "{a} and {b} show an inconsistent pattern ({a} {dir_a}, {b} {dir_b}), not a clear shared direction.",
}


def _execute_compare_marker_trends(ctx: ToolExecutionContext, concept_id_a: str, concept_id_b: str) -> ToolExecutionResult:
    call = PlannedToolCall("compare_marker_trends", {"concept_id_a": concept_id_a, "concept_id_b": concept_id_b})
    trend_a = compute_trend(ctx.bloodwork, concept_id_a)
    trend_b = compute_trend(ctx.bloodwork, concept_id_b)
    name_a, name_b = _marker_display_name(ctx, concept_id_a), _marker_display_name(ctx, concept_id_b)

    unavailable = [(name_a, trend_a), (name_b, trend_b)]
    unavailable = [(name, t) for name, t in unavailable if not t.available]
    if unavailable:
        # Honest partial failure, not a guess: if either marker's own trend
        # can't be determined (missing data, only one point, mismatched
        # units -- same reasons get_marker_trend already refuses to guess
        # for), there is nothing safe to compare it against either.
        details = "; ".join(f"{name}: {t.reason_unavailable}" for name, t in unavailable)
        return ToolExecutionResult(
            call=call,
            ok=True,
            result_summary=f"comparison unavailable: {details}",
            limitations=[Limitation(kind="trend_unavailable", detail=details)],
        )

    fact_a = _build_trend_fact(ctx, concept_id_a, trend_a)
    fact_b = _build_trend_fact(ctx, concept_id_b, trend_b)
    assert trend_a.direction is not None and trend_b.direction is not None  # guaranteed by TrendResult when available=True
    relationship = _compare_directions(trend_a.direction, trend_b.direction)
    relationship_fact = GroundedFact(
        claim=_RELATIONSHIP_CLAIM[relationship].format(a=name_a, b=name_b, dir_a=trend_a.direction, dir_b=trend_b.direction),
        source_type="bloodwork",
        source_ref=f"trend_comparison:{concept_id_a}:{concept_id_b}",
    )
    return ToolExecutionResult(
        call=call,
        ok=True,
        result_summary=f"{name_a} {trend_a.direction} vs {name_b} {trend_b.direction} ({relationship})",
        grounded_facts=[fact_a, fact_b, relationship_fact],
    )


def _execute_get_marker_snapshot(ctx: ToolExecutionContext, concept_id: str) -> ToolExecutionResult:
    display_name = _marker_display_name(ctx, concept_id)
    latest_panel = ctx.bloodwork.latest_panel
    marker = latest_panel.get(concept_id) if latest_panel is not None else None
    if latest_panel is None or marker is None:
        return ToolExecutionResult(
            call=PlannedToolCall("get_marker_snapshot", {"concept_id": concept_id}),
            ok=True,
            result_summary="no data",
            limitations=[Limitation(kind="missing_data", detail=f"No {display_name} measurement is available for this user.")],
        )
    fact = GroundedFact(
        claim=f"{marker.display_name} = {marker.value} {marker.unit} ({marker.classification}) on {latest_panel.measurement_date}",
        source_type="bloodwork",
        source_ref=f"{latest_panel.panel_id}:{concept_id}",
        numeric_values=(float(marker.value),),
        unit=marker.unit,
        display_name=marker.display_name,
        display_name_aliases=ctx.catalog.aliases_for(concept_id),
    )
    return ToolExecutionResult(
        call=PlannedToolCall("get_marker_snapshot", {"concept_id": concept_id}),
        ok=True,
        result_summary=f"{marker.display_name}={marker.value}{marker.unit} ({marker.classification})",
        grounded_facts=[fact],
        mentioned_markers={concept_id: marker},
    )


def _execute_get_focus_markers(ctx: ToolExecutionContext) -> ToolExecutionResult:
    latest_panel = ctx.bloodwork.latest_panel
    if latest_panel is None:
        return ToolExecutionResult(
            call=PlannedToolCall("get_focus_markers"),
            ok=True,
            result_summary="no panel available",
            limitations=[Limitation(kind="missing_data", detail="No bloodwork panel is available for this user.")],
        )
    items = rank_focus_markers(latest_panel, ctx.catalog, set())
    facts = [
        GroundedFact(
            claim=(
                f"{it.marker.display_name} = {it.marker.value} {it.marker.unit} "
                f"({it.marker.classification}) on {latest_panel.measurement_date}"
            ),
            source_type="bloodwork",
            source_ref=f"{latest_panel.panel_id}:{it.marker.concept_id}",
            numeric_values=(float(it.marker.value),),
            unit=it.marker.unit,
            display_name=it.marker.display_name,
            display_name_aliases=ctx.catalog.aliases_for(it.marker.concept_id),
        )
        for it in items
    ]
    return ToolExecutionResult(
        call=PlannedToolCall("get_focus_markers"),
        ok=True,
        result_summary=f"{len(items)} flagged marker(s): {[it.marker.concept_id for it in items]}",
        grounded_facts=facts,
        focus_items=items,
    )


_QUESTIONNAIRE_NUMBER_RE = re.compile(r"-?\d+\.?\d*")


def _execute_get_questionnaire_fact(ctx: ToolExecutionContext, field_name: str) -> ToolExecutionResult:
    fact = ctx.questionnaire.fact(field_name)
    if fact is None:
        return ToolExecutionResult(
            call=PlannedToolCall("get_questionnaire_fact", {"field": field_name}),
            ok=True,
            result_summary="not reported",
            limitations=[Limitation(kind="missing_data", detail=f"The questionnaire field {field_name!r} was not reported.")],
        )
    numbers = tuple(float(m) for m in _QUESTIONNAIRE_NUMBER_RE.findall(fact.value))
    grounded = GroundedFact(
        claim=f"Questionnaire {field_name}: {fact.value}",
        source_type="questionnaire",
        source_ref=f"questionnaire:{field_name}",
        numeric_values=numbers,
    )
    return ToolExecutionResult(
        call=PlannedToolCall("get_questionnaire_fact", {"field": field_name}),
        ok=True,
        result_summary=f"{field_name}={fact.value}",
        grounded_facts=[grounded],
    )


def _execute_get_supplement_cautions(ctx: ToolExecutionContext) -> ToolExecutionResult:
    modifiers = build_supplement_cautions(ctx.questionnaire, ctx.profile)
    facts = [mod.grounded_fact for mod in modifiers]
    return ToolExecutionResult(
        call=PlannedToolCall("get_supplement_cautions"),
        ok=True,
        result_summary=f"{len(modifiers)} caution(s)" if modifiers else "no supplement cautions reported",
        grounded_facts=facts,
    )


def _execute_get_allergies(ctx: ToolExecutionContext) -> ToolExecutionResult:
    if not ctx.profile.allergies:
        return ToolExecutionResult(
            call=PlannedToolCall("get_allergies"),
            ok=True,
            result_summary="no allergies reported",
            limitations=[Limitation(kind="missing_data", detail="No allergies are on file for this user.")],
        )
    names = [a.name for a in ctx.profile.allergies]
    fact = GroundedFact(
        claim=f"Reported allergies: {', '.join(names)}",
        source_type="questionnaire",
        source_ref="profile:allergies",
    )
    return ToolExecutionResult(
        call=PlannedToolCall("get_allergies"),
        ok=True,
        result_summary=f"{len(names)} allergy(ies): {names}",
        grounded_facts=[fact],
    )


def _execute_search_knowledge(ctx: ToolExecutionContext, query: str) -> ToolExecutionResult:
    chunks = ctx.retriever.retrieve(query, top_k=3)
    return ToolExecutionResult(
        call=PlannedToolCall("search_knowledge", {"query": query}),
        ok=True,
        result_summary=f"{len(chunks)} chunk(s): {[rc.chunk.id for rc in chunks]}",
        retrieved_chunks=chunks,
    )


_DISPATCH = {
    "get_marker_trend": lambda ctx, args: _execute_get_marker_trend(ctx, args["concept_id"]),
    "get_marker_snapshot": lambda ctx, args: _execute_get_marker_snapshot(ctx, args["concept_id"]),
    "compare_marker_trends": lambda ctx, args: _execute_compare_marker_trends(ctx, args["concept_id_a"], args["concept_id_b"]),
    "get_focus_markers": lambda ctx, args: _execute_get_focus_markers(ctx),
    "get_questionnaire_fact": lambda ctx, args: _execute_get_questionnaire_fact(ctx, args["field"]),
    "get_supplement_cautions": lambda ctx, args: _execute_get_supplement_cautions(ctx),
    "get_allergies": lambda ctx, args: _execute_get_allergies(ctx),
    "search_knowledge": lambda ctx, args: _execute_search_knowledge(ctx, args["query"]),
}


def execute_tool_call(call: PlannedToolCall, ctx: ToolExecutionContext) -> ToolExecutionResult:
    return _DISPATCH[call.tool_name](ctx, call.args)


# --------------------------------------------------------------------------
# Planner
# --------------------------------------------------------------------------


class ToolPlanner(Protocol):
    backend_name: str

    def propose_plan(self, question_text: str, repair_reason: str | None = None) -> ToolPlan: ...


def planner_prompt(question_text: str, repair_reason: str | None = None) -> str:
    base = f"""You are a tool-selection planner for a health-data assistant.
Interpret the question only; never compute or invent a clinical fact yourself.
Return one or more tool calls (in parallel, when independent) from this fixed set:
{[spec["name"] for spec in TOOL_SPECS]}
Only use a concept_id from this list: {sorted(SUPPORTED_CONCEPT_IDS)}.
If the question needs no tool (e.g. it's a greeting), return an empty call list.
Question: {question_text}
"""
    if repair_reason:
        base += f"\nYour previous plan was rejected: {repair_reason}\nReturn a corrected plan."
    return base


def run_compound_reasoning(
    question_text: str,
    ctx: ToolExecutionContext,
    planner: ToolPlanner,
    on_stage: Callable[[str], None] | None = None,
) -> tuple[Brief, list[ToolCall]]:
    """The V2 entry point: plan -> capability-gate -> execute -> build a
    `Brief`. Bounded to `MAX_ITERATIONS` planner calls total. A round
    that has any rejections gets one more repair attempt (not just a
    round where *everything* was rejected) -- newly-accepted calls are
    executed immediately each round, so a repair round only ever needs
    to fix the part that actually failed; nothing already executed is
    ever re-executed, even if a repair round's plan re-proposes it. If
    the bounded loop ends with no call ever having succeeded, this gives
    up honestly (an empty `Brief` with a `Limitation` explaining why,
    never a guess); if some calls succeeded but others remained rejected
    when the budget ran out, that's disclosed as a `Limitation` too, not
    silently dropped. See docs/DECISIONS.md, 2026-09-21 entries (F5's
    initial disclosure-only fix, then this repair-the-rejected-half
    enhancement).

    `on_stage`, if given, is called with a short human-readable string at
    each real checkpoint (planning, tool execution) -- purely an
    observability hook for a caller that wants to show live progress
    (see `dev_server.py`); it changes no behavior and defaults to doing
    nothing. This project's process is short (one planning round-trip in
    the common case, then fast in-process tool calls -- see `docs/
    DECISIONS.md`), so this is a small number of coarse-grained stage
    updates, not per-token streaming."""

    def stage(message: str) -> None:
        if on_stage is not None:
            on_stage(message)

    def call_signature(call: PlannedToolCall) -> tuple[str, tuple[tuple[str, str], ...]]:
        return (call.tool_name, tuple(sorted(call.args.items())))

    trace_calls: list[ToolCall] = []
    brief = Brief(intent=COMPOUND_REASONING)
    executed_signatures: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    all_executed_calls: list[PlannedToolCall] = []
    repair_reason: str | None = None
    # Outstanding (tool_name, reason) rejections, carried across rounds by
    # default -- only cleared for a given tool_name when a *genuinely new*
    # execution for it happens (see `new_calls` below), never just because
    # the round's own plan happened to contain no new rejections for it.
    #
    # An independent review found the previous version of this
    # (`unresolved = rejections`, wholesale-overwritten whenever a round
    # proposed anything at all) let a repair round silently erase
    # disclosure of an unrelated, still-unresolved failure: round 1
    # proposes a valid call plus an invalid one (1 succeeds, 1 rejected);
    # round 2 re-proposes *only* the already-succeeded call, addressing
    # nothing new. `plan.calls` is non-empty, so the old code overwrote
    # `unresolved` with this round's rejections (empty, since nothing was
    # rejected this round) -- the invalid-marker failure vanished with no
    # limitation ever recorded, not because it was fixed, but because
    # nothing in this round mentioned it again. See docs/DECISIONS.md.
    unresolved: list[tuple[str, str]] = []

    for _iteration in range(MAX_ITERATIONS):
        stage("Planning which tools to call..." if repair_reason is None else "Repairing the tool plan...")
        _plan_start = time.monotonic()
        plan = planner.propose_plan(question_text, repair_reason)
        trace_calls.append(
            ToolCall(
                name="propose_plan",
                args={"repair_reason": repair_reason} if repair_reason else {},
                result_summary=f"{len(plan.calls)} call(s) proposed: {[c.tool_name for c in plan.calls]}",
                duration_ms=round((time.monotonic() - _plan_start) * 1000, 1),
            )
        )
        round_rejections: list[tuple[str, str]] = []
        round_accepted: list[PlannedToolCall] = []
        for call in plan.calls:
            _gate_start = time.monotonic()
            allowed, reason = capability_gate(call)
            trace_calls.append(
                ToolCall(
                    name="capability_gate",
                    args={"tool_name": call.tool_name, **call.args},
                    result_summary="accepted" if allowed else f"rejected: {reason}",
                    ok=allowed,
                    duration_ms=round((time.monotonic() - _gate_start) * 1000, 1),
                )
            )
            if allowed:
                round_accepted.append(call)
            else:
                round_rejections.append((call.tool_name, reason or "rejected"))

        # Execute newly-accepted calls right away, this round -- a repair
        # round's plan may legitimately re-propose a call already
        # accepted+executed earlier alongside a fixed one; dedup by
        # signature so it's never run twice (which would double-count its
        # grounded facts).
        new_calls = [c for c in round_accepted if call_signature(c) not in executed_signatures]
        if new_calls:
            stage(f"Calling tools: {', '.join(c.tool_name for c in new_calls)}...")
            for call in new_calls:
                # A genuinely *new* execution for this tool_name is the
                # only mechanical signal available that this round's plan
                # actually addressed something previously broken, not
                # just re-proposed old work -- so it retires at most one
                # outstanding rejection sharing the tool name. A round
                # that re-proposes an already-executed call (excluded
                # from `new_calls` above) contributes nothing here, and
                # so can never clear an unrelated rejection by accident.
                for i, (unresolved_tool_name, _reason) in enumerate(unresolved):
                    if unresolved_tool_name == call.tool_name:
                        del unresolved[i]
                        break
                _exec_start = time.monotonic()
                result = execute_tool_call(call, ctx)
                executed_signatures.add(call_signature(call))
                all_executed_calls.append(call)
                trace_calls.append(
                    ToolCall(
                        name=call.tool_name,
                        args=call.args,
                        result_summary=result.result_summary,
                        ok=result.ok,
                        duration_ms=round((time.monotonic() - _exec_start) * 1000, 1),
                    )
                )
                brief.grounded_facts.extend(result.grounded_facts)
                brief.limitations.extend(result.limitations)
                brief.focus_items.extend(result.focus_items)
                brief.mentioned_markers.update(result.mentioned_markers)
                brief.retrieved_chunks.extend(result.retrieved_chunks)

        unresolved.extend(round_rejections)
        if not unresolved:
            break
        repair_reason = "; ".join(reason for _, reason in unresolved)

    if not all_executed_calls:
        brief.limitations.append(
            Limitation(
                kind="unsupported_request",
                detail="This question could not be mapped to any supported tool, even after a repair attempt.",
            )
        )
        return brief, trace_calls

    # Disclose, don't silently drop: the bounded repair budget ran out
    # with something still rejected. Reached only when `unresolved` is
    # still non-empty after the loop -- i.e. every repair attempt within
    # `MAX_ITERATIONS` either failed again or the planner gave up on it.
    for _, reason in unresolved:
        brief.limitations.append(Limitation(kind="partial_tool_rejection", detail=reason))

    accepted = all_executed_calls

    # V1 (`agent.py`'s `ask()`) always retrieves knowledge-base context,
    # for every question, regardless of intent -- it's cheap and doesn't
    # need a model's judgment call the way tool selection does. V2 left
    # this entirely to the planner's discretion (only via an explicit
    # `search_knowledge` call), and found live -- across roughly ten real
    # runs testing the browser demo -- that the planner reliably chose
    # not to call it for data-lookup-shaped questions, even when the
    # answer would clearly benefit from the project's own vetted
    # background material. Restore V1's behavior as a deterministic
    # fallback: only when the planner didn't already explicitly search
    # (respecting its own, more specific query when it did), retrieve
    # using the raw question text, scoped by whatever markers the
    # accepted tool calls actually touched -- the same `CONCEPT_TOPIC_
    # TAGS` mapping `ask()` already uses, not a new one. See docs/
    # DECISIONS.md, 2026-09-20 entry.
    if not any(call.tool_name == "search_knowledge" for call in accepted):
        stage("Searching the knowledge base...")
        _retrieve_start = time.monotonic()
        topic_tags: set[str] = set()
        for call in accepted:
            concept_id = call.args.get("concept_id")
            if concept_id:
                topic_tags |= CONCEPT_TOPIC_TAGS.get(concept_id, set())
        retrieved = ctx.retriever.retrieve(question_text, top_k=6, topic_filter=topic_tags)
        brief.retrieved_chunks.extend(retrieved)
        trace_calls.append(
            ToolCall(
                name="retrieve_knowledge",
                args={"query": question_text, "topic_filter": sorted(topic_tags)},
                result_summary=f"{len(retrieved)} chunks: {[rc.chunk.id for rc in retrieved]}",
                duration_ms=round((time.monotonic() - _retrieve_start) * 1000, 1),
            )
        )

    return brief, trace_calls
