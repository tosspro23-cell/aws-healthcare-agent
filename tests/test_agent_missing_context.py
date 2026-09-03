"""Integration test for q_missing_context: trend question with no prior data
for the requested marker. Must not invent a trend direction."""

QUESTION = "Can you tell me if my glucose got worse?"


def test_missing_trend_is_safe(agent):
    response = agent.ask(user_id="user_demo_001", question_text=QUESTION, question_id="q_missing_context")
    assert response.safe is True


def test_missing_trend_does_not_claim_direction(agent):
    response = agent.ask(user_id="user_demo_001", question_text=QUESTION)
    lowered = response.answer.lower()
    assert "got worse" not in lowered
    assert "improved" not in lowered
    assert "trend" not in lowered or "cannot be determined" in lowered or "can't be determined" in lowered


def test_missing_trend_states_limitation(agent):
    response = agent.ask(user_id="user_demo_001", question_text=QUESTION)
    assert "Limitation" in response.answer or "cannot be determined" in response.answer.lower()


def test_missing_trend_uses_latest_value(agent):
    response = agent.ask(user_id="user_demo_001", question_text=QUESTION)
    assert "108" in response.answer  # fasting glucose latest value


def test_missing_trend_mentions_previous_panel_checked(agent):
    response = agent.ask(user_id="user_demo_001", question_text=QUESTION)
    assert "2025-12-08" in response.answer
