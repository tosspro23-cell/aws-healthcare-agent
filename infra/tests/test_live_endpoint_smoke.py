"""End-to-end smoke test against a *deployed* API Gateway endpoint.

Skipped entirely unless CARE_AGENT_API_URL is set -- this never runs in CI
and never runs as part of a normal `pytest` invocation. It exists for the
Phase 1 acceptance check: "a request against the live endpoint returns the
same answer as running `care-agent ask` locally for the same question."

Usage, after `cdk deploy` (see docs/AWS_ROADMAP.md):

    export CARE_AGENT_API_URL="$(aws cloudformation describe-stacks \\
        --stack-name CareAgentApiStack \\
        --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \\
        --output text)"
    pytest infra/tests/test_live_endpoint_smoke.py -v

Uses stdlib `urllib` rather than `requests`, consistent with the rest of
this project's zero-extra-dependency-by-default approach.
"""

import json
import os
import urllib.error
import urllib.request

import pytest

API_URL = os.environ.get("CARE_AGENT_API_URL")

pytestmark = pytest.mark.skipif(not API_URL, reason="CARE_AGENT_API_URL not set -- no deployed endpoint to test against")


def _post_ask(payload: dict) -> tuple[int, dict]:
    url = API_URL.rstrip("/") + "/ask"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_live_main_question_matches_local_behavior():
    status, payload = _post_ask(
        {
            "user_id": "user_demo_001",
            "question": "My LDL and HbA1c are high. What should I focus on first, and does my questionnaire change the advice?",
        }
    )
    assert status == 200
    assert payload["safe"] is True
    assert "162" in payload["answer"]
    assert "6.1" in payload["answer"]
    assert payload["trace"]["intent"] == "priority_focus"
    assert payload["trace"]["narrator_backend"] == "mock"


def test_live_missing_fields_returns_400():
    status, payload = _post_ask({"question": "hello"})
    assert status == 400
    assert "error" in payload


def test_live_unknown_user_returns_404():
    status, payload = _post_ask({"user_id": "not_a_real_user", "question": "hi"})
    assert status == 404


def test_live_supplement_question_gives_no_dose():
    status, payload = _post_ask({"user_id": "user_demo_001", "question": "Should I take supplements for cholesterol?"})
    assert status == 200
    assert payload["safe"] is True
    lowered = payload["answer"].lower()
    assert "mg" not in lowered or "162 mg/dl" in lowered  # the LDL value itself is fine; a dose isn't
