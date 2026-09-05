"""End-to-end smoke test against a *deployed* API Gateway endpoint.

Skipped entirely unless CARE_AGENT_API_URL is set -- this never runs in CI
and never runs as part of a normal `pytest` invocation.

Since Phase 2, `/ask` requires a valid Cognito JWT (see
`../stacks/api_stack.py`): a request with no/invalid token never reaches
the Lambda at all, so most of these cases additionally need
CARE_AGENT_ACCESS_TOKEN to actually exercise Lambda-level behavior. The
no-token case is deliberately its own always-runs-if-URL-is-set test.

Usage, after `cdk deploy` (see docs/AWS_ROADMAP.md):

    export CARE_AGENT_API_URL="$(aws cloudformation describe-stacks \\
        --stack-name CareAgentApiStack \\
        --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \\
        --output text)"
    eval "$(python infra/scripts/get_dev_token.py | grep ^export)"
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
ACCESS_TOKEN = os.environ.get("CARE_AGENT_ACCESS_TOKEN")

pytestmark = pytest.mark.skipif(not API_URL, reason="CARE_AGENT_API_URL not set -- no deployed endpoint to test against")

requires_token = pytest.mark.skipif(
    not ACCESS_TOKEN, reason="CARE_AGENT_ACCESS_TOKEN not set -- run infra/scripts/get_dev_token.py first"
)


def _post_ask(payload: dict, *, authorized: bool = True) -> tuple[int, dict]:
    url = API_URL.rstrip("/") + "/ask"
    headers = {"Content-Type": "application/json"}
    if authorized and ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {ACCESS_TOKEN}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            # API Gateway's own 401 (not our Lambda's JSON error shape) is
            # plain text, not JSON -- still a valid, expected response.
            return exc.code, {"raw": body}


def test_live_ask_without_token_returns_401():
    """Phase 2 acceptance check: the route now actually enforces auth --
    this must fail *before* ever reaching the Lambda, regardless of whether
    a valid CARE_AGENT_ACCESS_TOKEN happens to be set for other tests in this
    file (deliberately calls with authorized=False)."""
    status, _payload = _post_ask({"user_id": "user_demo_001", "question": "hello"}, authorized=False)
    assert status == 401


def test_live_ask_with_garbage_token_returns_401():
    url = API_URL.rstrip("/") + "/ask"
    request = urllib.request.Request(
        url,
        data=json.dumps({"user_id": "user_demo_001", "question": "hello"}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer not-a-real-jwt"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            raise AssertionError("expected an HTTPError for a garbage token")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401


@requires_token
def test_live_main_question_returns_a_real_safe_grounded_answer():
    """Regression test: this originally asserted the deployed endpoint's
    answer byte-for-byte matched a local mock-narrator run, including
    `narrator_backend == "mock"` -- true when this was written (Phase 1/2,
    before Bedrock was wired into the deployed Lambda), but stale ever
    since: the deployed AskHandler now defaults to
    CARE_AGENT_NARRATOR_BACKEND=bedrock (see docs/PHASE4_BEDROCK_EVIDENCE.md),
    a real LLM whose exact phrasing isn't byte-reproducible run to run. An
    independent review caught this test asserting something no longer
    true about the deployed system. What's still verifiable without
    assuming exact phrasing: the response is safe, actually answers the
    question (the grounded numeric values appear, since the safety net
    would otherwise have forced a fallback), and reports which backend
    actually produced it."""
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
    assert payload["trace"]["narrator_backend"] in ("mock", "bedrock")
    fallback_checks = [c for c in payload["trace"]["safety_checks"] if c["name"] == "narrator_fallback"]
    if payload["trace"]["narrator_backend"] == "bedrock":
        # If Bedrock's own output was used, it must not have needed the
        # fallback -- a fallback would mean narrator_backend was already
        # corrected to "mock" (see agent.py), so this is really asserting
        # internal consistency, not a new claim.
        assert not fallback_checks


@requires_token
def test_live_missing_fields_returns_400():
    status, payload = _post_ask({"question": "hello"})
    assert status == 400
    assert "error" in payload


@requires_token
def test_live_unknown_user_returns_404():
    status, payload = _post_ask({"user_id": "not_a_real_user", "question": "hi"})
    assert status == 404


@requires_token
def test_live_supplement_question_gives_no_dose():
    status, payload = _post_ask({"user_id": "user_demo_001", "question": "Should I take supplements for cholesterol?"})
    assert status == 200
    assert payload["safe"] is True
    lowered = payload["answer"].lower()
    assert "mg" not in lowered or "162 mg/dl" in lowered  # the LDL value itself is fine; a dose isn't
