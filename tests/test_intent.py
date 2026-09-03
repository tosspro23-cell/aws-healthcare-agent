from care_agent.intent import GENERAL, PRIORITY_FOCUS, RED_FLAG, SUPPLEMENT_SAFETY, TREND_CHECK, classify


def test_main_question_is_priority_focus():
    r = classify("My LDL and HbA1c are high. What should I focus on first, and does my questionnaire change the advice?")
    assert r.intent == PRIORITY_FOCUS


def test_missing_context_question_is_trend_check():
    r = classify("Can you tell me if my glucose got worse?")
    assert r.intent == TREND_CHECK


def test_supplements_question_is_supplement_safety():
    r = classify("Should I take supplements for cholesterol?")
    assert r.intent == SUPPLEMENT_SAFETY


def test_red_flag_chest_pain():
    r = classify("I'm having chest pain and can't breathe, what should I do?")
    assert r.intent == RED_FLAG


def test_red_flag_takes_priority_over_supplement_keywords():
    r = classify("I took a vitamin supplement and now I'm having chest pain")
    assert r.intent == RED_FLAG


def test_unrelated_question_falls_back_to_general():
    r = classify("What is a biomarker?")
    assert r.intent == GENERAL


def test_empty_question_falls_back_to_general():
    r = classify("")
    assert r.intent == GENERAL
