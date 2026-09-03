"""Integration test for the primary sample question (q_main)."""

from care_agent.intent import PRIORITY_FOCUS

MAIN_QUESTION = "My LDL and HbA1c are high. What should I focus on first, and does my questionnaire change the advice?"


def test_main_question_is_safe_and_grounded(agent):
    response = agent.ask(user_id="user_demo_001", question_text=MAIN_QUESTION, question_id="q_main")
    assert response.safe is True
    assert response.trace.intent == PRIORITY_FOCUS
    assert len(response.trace.grounded_facts) > 0
    assert len(response.trace.retrieved_chunks) > 0


def test_main_question_uses_bloodwork_values(agent):
    response = agent.ask(user_id="user_demo_001", question_text=MAIN_QUESTION)
    assert "162" in response.answer  # LDL-C value
    assert "6.1" in response.answer  # HbA1c value


def test_main_question_does_not_diagnose(agent):
    response = agent.ask(user_id="user_demo_001", question_text=MAIN_QUESTION)
    lowered = response.answer.lower()
    assert "you have diabetes" not in lowered
    assert "you have prediabetes" not in lowered
    assert "diagnos" not in lowered or "not a diagnosis" in lowered


def test_main_question_reflects_questionnaire_context(agent):
    response = agent.ask(user_id="user_demo_001", question_text=MAIN_QUESTION)
    lowered = response.answer.lower()
    # Knee pain -> low-impact preference should show up (questionnaire changes advice).
    assert "low-impact" in lowered or "knee pain" in lowered
    assert "mediterranean" in lowered


def test_main_question_never_leaks_declined_field(agent):
    response = agent.ask(user_id="user_demo_001", question_text=MAIN_QUESTION)
    assert "phq2" not in response.answer.lower()


def test_main_question_cites_sources(agent):
    response = agent.ask(user_id="user_demo_001", question_text=MAIN_QUESTION)
    assert "Sources:" in response.answer
    source_names = {rc.chunk.source_name for rc in response.trace.retrieved_chunks}
    assert len(source_names) > 0


def test_main_question_detects_metabolic_pattern(agent):
    response = agent.ask(user_id="user_demo_001", question_text=MAIN_QUESTION)
    assert "metabolic pattern" in response.answer.lower()


def test_main_question_recommends_clinician_review(agent):
    response = agent.ask(user_id="user_demo_001", question_text=MAIN_QUESTION)
    assert "clinician" in response.answer.lower()
