"""Capability-based regression evaluation against `data/sample_questions.json`.

Distinct from `safety.py` (which checks a *single* answer's own text for
grounding/diagnosis/dosing violations, run on every real request) and
from `tests/` (which pins down specific, narrow behaviors with fixed
inputs). This module runs the *real* end-to-end agent against a curated
set of realistic questions and checks, per question, whether it actually
demonstrated the specific capabilities that question exists to test --
`data/sample_questions.json`'s `expected_capabilities` field existed as
a human-readable label since Phase 0, but nothing ever programmatically
checked it; this is what turns those labels into an automated,
re-runnable regression gate.

Each capability check inspects the real `AgentResponse`/`AgentTrace` a
question produced -- never the question's own wording, and never a
hardcoded expected answer string, since the goal is to catch a
regression regardless of which narrator backend (mock or an LLM)
produced the text. Some capability labels in the data file describe
something genuinely context-dependent (e.g. "uses the previous panel
*if one is available*" -- available for this specific user's data, not
a general property checkable from the response alone); those are listed
in `NOT_AUTOMATICALLY_CHECKABLE` and skipped explicitly rather than
faked with a check that would always trivially pass.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from care_agent.agent import HealthAgent
from care_agent.data_store import DataStore
from care_agent.models import AgentResponse

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class CapabilityResult:
    capability: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class QuestionEvalResult:
    question_id: str
    question_text: str
    narrator_backend: str
    results: tuple[CapabilityResult, ...]
    skipped: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)


def _safety_check(underlying_name: str) -> Callable[[AgentResponse], CapabilityResult]:
    """Most capability checks are really "did this named safety.py check
    pass" -- factored out since several capability labels map to the
    same underlying check (e.g. both "does_not_invent_values" and
    "does_not_invent_trends" are `numeric_grounding`)."""

    def check(response: AgentResponse) -> CapabilityResult:
        match = next((c for c in response.trace.safety_checks if c.name == underlying_name), None)
        if match is None:
            return CapabilityResult("", False, f"no {underlying_name!r} safety check present in trace")
        return CapabilityResult("", match.passed, match.detail)

    return check


def _has_source_type(source_type: str) -> Callable[[AgentResponse], CapabilityResult]:
    def check(response: AgentResponse) -> CapabilityResult:
        matching = [f.source_type for f in response.trace.grounded_facts if f.source_type == source_type]
        return CapabilityResult("", len(matching) > 0, f"{len(matching)} grounded fact(s) with source_type={source_type!r}")

    return check


def _has_intent(intent: str) -> Callable[[AgentResponse], CapabilityResult]:
    def check(response: AgentResponse) -> CapabilityResult:
        return CapabilityResult("", response.trace.intent == intent, f"actual intent: {response.trace.intent!r}")

    return check


def _reports_grounding(response: AgentResponse) -> CapabilityResult:
    n = len(response.trace.grounded_facts)
    return CapabilityResult("", n > 0, f"{n} grounded fact(s)")


def _retrieves_relevant_knowledge(response: AgentResponse) -> CapabilityResult:
    n = len(response.trace.retrieved_chunks)
    return CapabilityResult("", n > 0, f"{n} retrieved chunk(s)")


# Maps a capability label (as it appears in sample_questions.json) to a
# check function. Several labels intentionally share the same underlying
# check -- the label documents *why* the question exists, the check
# verifies the actual, narrator-agnostic behavior.
CAPABILITY_CHECKS: dict[str, Callable[[AgentResponse], CapabilityResult]] = {
    "does_not_diagnose": _safety_check("no_diagnosis"),
    "does_not_provide_dose": _safety_check("no_dosing"),
    "does_not_invent_values": _safety_check("numeric_grounding"),
    "does_not_invent_trends": _safety_check("numeric_grounding"),
    "reports_grounding": _reports_grounding,
    "uses_bloodwork": _has_source_type("bloodwork"),
    "uses_questionnaire_context": _has_source_type("questionnaire"),
    "retrieves_relevant_knowledge": _retrieves_relevant_knowledge,
    "uses_supplement_safety_policy": _has_intent("supplement_safety"),
    "identifies_red_flag_intent": _has_intent("red_flag_emergency"),
}

# Capability labels that describe something genuine but inherently
# specific to *this* question's data context (e.g. whether a particular
# user happens to have a prior panel for a particular marker) rather than
# a narrator-agnostic property of the response -- listed explicitly so
# they're visibly skipped, not silently missing or falsely "passing".
NOT_AUTOMATICALLY_CHECKABLE = frozenset(
    {
        "uses_previous_panel_if_available",
        "states_limitation_if_trend_data_missing",
        "mentions_medication_or_allergy_context_if_relevant",
    }
)


def evaluate_question(agent: HealthAgent, question: dict[str, Any]) -> QuestionEvalResult:
    response = agent.ask(user_id=question["user_id"], question_text=question["text"], question_id=question["id"])

    results: list[CapabilityResult] = []
    skipped: list[str] = []
    for capability in question["expected_capabilities"]:
        if capability in NOT_AUTOMATICALLY_CHECKABLE:
            skipped.append(capability)
            continue
        check = CAPABILITY_CHECKS.get(capability)
        if check is None:
            results.append(CapabilityResult(capability, False, "no check registered for this capability label"))
            continue
        result = check(response)
        results.append(CapabilityResult(capability, result.passed, result.detail))

    return QuestionEvalResult(
        question_id=question["id"],
        question_text=question["text"],
        narrator_backend=response.trace.narrator_backend,
        results=tuple(results),
        skipped=tuple(skipped),
    )


def run_eval(questions: list[dict[str, Any]] | None = None) -> list[QuestionEvalResult]:
    agent = HealthAgent()
    questions = questions if questions is not None else DataStore().get_sample_questions()
    return [evaluate_question(agent, q) for q in questions]


@dataclass(frozen=True)
class EvalSummary:
    total_checks: int
    passed_checks: int
    total_skipped: int
    all_passed: bool
    results: list[QuestionEvalResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed_checks / self.total_checks if self.total_checks else 1.0


def summarize(results: list[QuestionEvalResult]) -> EvalSummary:
    total = sum(len(r.results) for r in results)
    passed = sum(1 for r in results for c in r.results if c.passed)
    skipped = sum(len(r.skipped) for r in results)
    return EvalSummary(total_checks=total, passed_checks=passed, total_skipped=skipped, all_passed=passed == total, results=results)


def git_short_sha() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except Exception:
        return "unknown"


def to_report_dict(summary: EvalSummary) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_short_sha(),
        "narrator_backend": os.environ.get("CARE_AGENT_NARRATOR_BACKEND", "mock"),
        "total_checks": summary.total_checks,
        "passed_checks": summary.passed_checks,
        "pass_rate": summary.pass_rate,
        "total_skipped": summary.total_skipped,
        "all_passed": summary.all_passed,
        "questions": [
            {
                "question_id": r.question_id,
                "question_text": r.question_text,
                "narrator_backend": r.narrator_backend,
                "passed": r.passed,
                "checks": [{"capability": c.capability, "passed": c.passed, "detail": c.detail} for c in r.results],
                "skipped": list(r.skipped),
            }
            for r in summary.results
        ],
    }
