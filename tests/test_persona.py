"""Persona (patient/clinician) narration tests -- see docs/DECISIONS.md,
2026-09-20 entry. Persona changes disclaimer framing only; the fact
content and the safety gate are identical for both (covered by
`test_orchestrator.py::test_ask_compound_clinician_persona_changes_disclaimer_not_facts`
for the V2 path)."""

from __future__ import annotations

from care_agent.agent import HealthAgent
from care_agent.narrator._prompt import CLINICIAN_SYSTEM_PROMPT, PATIENT_SYSTEM_PROMPT, system_prompt_for
from care_agent.narrator.mock_narrator import _general_disclaimer_line, _not_a_diagnosis_line


def test_system_prompt_for_defaults_to_patient():
    assert system_prompt_for("patient") == PATIENT_SYSTEM_PROMPT
    assert system_prompt_for("anything_else") == PATIENT_SYSTEM_PROMPT  # unrecognized persona is not clinician


def test_system_prompt_for_clinician_addresses_the_clinician_not_the_patient():
    prompt = system_prompt_for("clinician")
    assert prompt == CLINICIAN_SYSTEM_PROMPT
    assert "the patient" in prompt.lower()
    assert "clinician" in prompt.lower()


def test_mock_narrator_disclaimer_helpers_differ_by_persona():
    assert _not_a_diagnosis_line("patient") != _not_a_diagnosis_line("clinician")
    assert _general_disclaimer_line("patient") != _general_disclaimer_line("clinician")
    assert "clinical judgment" in _not_a_diagnosis_line("clinician").lower()
    assert "clinical judgment" in _general_disclaimer_line("clinician").lower()


def test_ask_defaults_to_patient_persona_unchanged_from_before_this_feature():
    """Backward-compatibility check: every existing caller of `ask()` that
    doesn't pass `persona` gets byte-identical behavior to before persona
    support existed."""
    agent = HealthAgent()
    response = agent.ask(user_id="user_demo_001", question_text="What should I focus on first?")
    assert "This is not a diagnosis, and it doesn't replace a clinician's interpretation of your full history." in response.answer


def test_ask_clinician_persona_changes_the_disclaimer_line():
    agent = HealthAgent()
    response = agent.ask(user_id="user_demo_001", question_text="What should I focus on first?", persona="clinician")
    assert response.safe is True
    assert "decision-support summary" in response.answer.lower()
    assert "doesn't replace a clinician's interpretation of your full history" not in response.answer
