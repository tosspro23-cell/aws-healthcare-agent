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


def test_vitamin_d_trend_question_is_trend_check_not_supplement_safety():
    """Regression test: found live-testing the Workbench. "Vitamin D" is
    both a biomarker name and a word `_SUPPLEMENT_PATTERNS` matches on its
    own, so a genuine trend question naming that marker used to get
    force-classified as supplement_safety before trend_check ever got a
    chance -- trend computation never ran, and the narrator was left to
    guess at data-availability claims no one had actually verified."""
    r = classify("Has my vitamin D changed since last time?")
    assert r.intent == TREND_CHECK


def test_vitamin_supplement_question_without_trend_language_is_still_supplement_safety():
    """The fix above must not regress the case with no trend/priority
    language present at all -- a bare marker-name mention should still
    route to supplement_safety, matching this project's existing
    behavior for that case."""
    r = classify("What vitamin should I take for my low levels?")
    assert r.intent == SUPPLEMENT_SAFETY


def test_unrelated_question_falls_back_to_general():
    r = classify("What is a biomarker?")
    assert r.intent == GENERAL


def test_empty_question_falls_back_to_general():
    r = classify("")
    assert r.intent == GENERAL
