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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from care_agent.catalog import BiomarkerCatalog
from care_agent.intent import COMPOUND_REASONING
from care_agent.models import Bloodwork, GroundedFact, Limitation, QuestionnaireContext, ToolCall, UserProfile
from care_agent.plausibility import SUPPORTED_CONCEPT_IDS
from care_agent.reasoning import CONCEPT_TOPIC_TAGS, Brief, FocusItem, build_supplement_cautions, rank_focus_markers
from care_agent.retrieval.base import Retriever
from care_agent.trend import compute_trend

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
    assert trend.latest_value is not None and trend.previous_value is not None  # guaranteed by TrendResult when available=True
    fact = GroundedFact(
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
    )
    return ToolExecutionResult(
        call=PlannedToolCall("get_marker_trend", {"concept_id": concept_id}),
        ok=True,
        result_summary=f"{display_name} {trend.direction}: {trend.previous_value}->{trend.latest_value} {trend.unit}",
        grounded_facts=[fact],
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
    # The most recent round's rejections still needing a repair attempt.
    # Only overwritten by a round that actually proposed at least one
    # call -- an empty repair-round response (the planner effectively
    # giving up) must not silently erase what the *previous* round
    # already found rejected. See docs/DECISIONS.md, 2026-09-21 entry.
    unresolved: list[str] = []

    for _iteration in range(MAX_ITERATIONS):
        stage("Planning which tools to call..." if repair_reason is None else "Repairing the tool plan...")
        plan = planner.propose_plan(question_text, repair_reason)
        trace_calls.append(
            ToolCall(
                name="propose_plan",
                args={"repair_reason": repair_reason} if repair_reason else {},
                result_summary=f"{len(plan.calls)} call(s) proposed: {[c.tool_name for c in plan.calls]}",
            )
        )
        rejections: list[str] = []
        round_accepted: list[PlannedToolCall] = []
        for call in plan.calls:
            allowed, reason = capability_gate(call)
            trace_calls.append(
                ToolCall(
                    name="capability_gate",
                    args={"tool_name": call.tool_name, **call.args},
                    result_summary="accepted" if allowed else f"rejected: {reason}",
                    ok=allowed,
                )
            )
            if allowed:
                round_accepted.append(call)
            else:
                rejections.append(reason or "rejected")

        # Execute newly-accepted calls right away, this round -- a repair
        # round's plan may legitimately re-propose a call already
        # accepted+executed earlier alongside a fixed one; dedup by
        # signature so it's never run twice (which would double-count its
        # grounded facts).
        new_calls = [c for c in round_accepted if call_signature(c) not in executed_signatures]
        if new_calls:
            stage(f"Calling tools: {', '.join(c.tool_name for c in new_calls)}...")
            for call in new_calls:
                result = execute_tool_call(call, ctx)
                executed_signatures.add(call_signature(call))
                all_executed_calls.append(call)
                trace_calls.append(ToolCall(name=call.tool_name, args=call.args, result_summary=result.result_summary, ok=result.ok))
                brief.grounded_facts.extend(result.grounded_facts)
                brief.limitations.extend(result.limitations)
                brief.focus_items.extend(result.focus_items)
                brief.mentioned_markers.update(result.mentioned_markers)
                brief.retrieved_chunks.extend(result.retrieved_chunks)

        if plan.calls:
            unresolved = rejections
        if not unresolved:
            break
        repair_reason = "; ".join(unresolved)

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
    for reason in unresolved:
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
            )
        )

    return brief, trace_calls
