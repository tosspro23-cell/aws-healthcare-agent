"""Tests for care_agent.eval -- the capability-based regression harness
run against data/sample_questions.json's real, curated question set.

The most important test here is the last one: it runs every shipped
sample question through the real mock-narrator agent and asserts every
checkable capability passes. That's the actual regression gate this
module exists to provide -- run automatically by `pytest` (already part
of CI) rather than only when someone remembers to run the separate
`eval-capabilities` CLI command by hand.
"""

from __future__ import annotations

import json

from care_agent.agent import HealthAgent
from care_agent.data_store import DataStore
from care_agent.eval import (
    CAPABILITY_CHECKS,
    NOT_AUTOMATICALLY_CHECKABLE,
    CapabilityResult,
    QuestionEvalResult,
    evaluate_question,
    run_eval,
    summarize,
    to_report_dict,
)
from care_agent.models import AgentResponse, AgentTrace, GroundedFact, SafetyCheck


def _response(*, intent="general_bloodwork_question", safety_checks=(), grounded_facts=(), retrieved_chunks=()) -> AgentResponse:
    trace = AgentTrace(
        question_id="q_test",
        user_id="user_demo_001",
        intent=intent,
        grounded_facts=list(grounded_facts),
        retrieved_chunks=list(retrieved_chunks),
        safety_checks=list(safety_checks),
    )
    return AgentResponse(answer="an answer", trace=trace, safe=all(c.passed for c in safety_checks))


def test_every_capability_label_in_the_dataset_is_either_checked_or_explicitly_skipped():
    """Regression guard against drift: a typo'd or newly-added capability
    label in sample_questions.json that matches neither a registered
    check nor the explicit skip list would otherwise silently produce a
    hard failure with a confusing message, or (worse, if this guard
    didn't exist) simply be ignored."""
    all_labels = {c for q in DataStore().get_sample_questions() for c in q["expected_capabilities"]}
    unrecognized = all_labels - set(CAPABILITY_CHECKS) - NOT_AUTOMATICALLY_CHECKABLE
    assert unrecognized == set()


def test_safety_check_capability_passes_when_the_underlying_check_passed():
    response = _response(safety_checks=[SafetyCheck(name="no_diagnosis", passed=True)])
    result = CAPABILITY_CHECKS["does_not_diagnose"](response)
    assert result.passed is True


def test_safety_check_capability_fails_when_the_underlying_check_failed():
    response = _response(safety_checks=[SafetyCheck(name="no_diagnosis", passed=False, detail="matched a pattern")])
    result = CAPABILITY_CHECKS["does_not_diagnose"](response)
    assert result.passed is False
    assert "matched a pattern" in result.detail


def test_safety_check_capability_fails_closed_when_the_check_is_absent_from_the_trace():
    """If a trace somehow doesn't carry the safety check a capability
    depends on, that must count as a failure, not a silent pass -- an
    empty list here should never be mistaken for "nothing to complain
    about"."""
    response = _response(safety_checks=[])
    result = CAPABILITY_CHECKS["does_not_diagnose"](response)
    assert result.passed is False


def test_uses_bloodwork_capability_checks_source_type_not_just_presence():
    response = _response(grounded_facts=[GroundedFact(claim="c", source_type="questionnaire", source_ref="r")])
    assert CAPABILITY_CHECKS["uses_bloodwork"](response).passed is False

    response = _response(grounded_facts=[GroundedFact(claim="c", source_type="bloodwork", source_ref="r")])
    assert CAPABILITY_CHECKS["uses_bloodwork"](response).passed is True


def test_retrieves_relevant_knowledge_requires_at_least_one_chunk():
    response = _response(retrieved_chunks=[])
    assert CAPABILITY_CHECKS["retrieves_relevant_knowledge"](response).passed is False


def test_identifies_red_flag_intent_checks_the_actual_classified_intent():
    response = _response(intent="priority_focus")
    assert CAPABILITY_CHECKS["identifies_red_flag_intent"](response).passed is False

    response = _response(intent="red_flag_emergency")
    assert CAPABILITY_CHECKS["identifies_red_flag_intent"](response).passed is True


def test_evaluate_question_skips_not_automatically_checkable_labels_rather_than_faking_a_result():
    question = {
        "id": "q_fixture",
        "user_id": "user_demo_001",
        "text": "Can you tell me if my glucose got worse?",
        "expected_capabilities": ["does_not_invent_trends", "states_limitation_if_trend_data_missing"],
    }
    result = evaluate_question(HealthAgent(), question)
    assert "states_limitation_if_trend_data_missing" in result.skipped
    assert all(r.capability != "states_limitation_if_trend_data_missing" for r in result.results)


def test_summarize_counts_skipped_separately_from_pass_fail():
    fake = QuestionEvalResult(
        question_id="q1",
        question_text="t",
        narrator_backend="mock",
        results=(CapabilityResult("a", True), CapabilityResult("b", False)),
        skipped=("c",),
    )
    summary = summarize([fake])
    assert summary.total_checks == 2
    assert summary.passed_checks == 1
    assert summary.total_skipped == 1
    assert summary.all_passed is False


def test_to_report_dict_is_json_serializable_and_carries_the_expected_shape():
    first_question = DataStore().get_sample_questions()[0]
    summary = summarize([evaluate_question(HealthAgent(), first_question)])
    report = to_report_dict(summary)
    json.dumps(report)  # raises if anything isn't serializable
    assert report["total_checks"] > 0
    assert report["questions"][0]["question_id"] == "q_main"


def test_all_sample_questions_pass_their_expected_capabilities():
    """The actual regression gate: every shipped sample question must
    demonstrate every checkable capability it's supposed to, against the
    real mock-narrator agent. A capability regression in reasoning.py,
    safety.py, or the mock narrator should fail here, not just be
    described as "expected" in a data file nothing ever checks."""
    results = run_eval()
    summary = summarize(results)
    failures = [(r.question_id, c.capability, c.detail) for r in results for c in r.results if not c.passed]
    assert summary.all_passed, f"capability failures: {failures}"
