"""Unit tests for the optional Bedrock narrator.

Skipped entirely (via ``importorskip``) unless the ``boto3`` package is
installed -- CI's default `pip install -e ".[dev]"` does not install it, so
these tests never run there. The SDK client itself is mocked, so no network
call or real AWS credentials are ever needed even when the package is
present.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("boto3")

from care_agent.narrator.bedrock_narrator import BedrockNarrator  # noqa: E402


def _fake_bedrock_client(reply_text: str) -> MagicMock:
    client = MagicMock()
    client.converse.return_value = {
        "output": {"message": {"role": "assistant", "content": [{"text": reply_text}]}},
        "stopReason": "end_turn",
    }
    return client


def test_compose_sends_grounded_text_and_returns_reply():
    fake_client = _fake_bedrock_client("Rephrased but still grounded answer with LDL-C 162 mg/dL.")

    with patch("boto3.client", return_value=fake_client):
        narrator = BedrockNarrator(model_id="anthropic.claude-haiku-4-5-20251001-v1:0")

    from care_agent.models import UserProfile
    from care_agent.reasoning import Brief

    brief = Brief(intent="general_bloodwork_question")
    profile = UserProfile(user_id="u1", display_name="Alex", age=42, sex="female", country="PT")

    result = narrator.compose(brief, "What should I focus on first?", profile)

    assert result == "Rephrased but still grounded answer with LDL-C 162 mg/dL."
    call_kwargs = fake_client.converse.call_args.kwargs
    assert call_kwargs["modelId"] == "anthropic.claude-haiku-4-5-20251001-v1:0"
    assert call_kwargs["system"][0]["text"]  # the shared SYSTEM_PROMPT was passed
    assert "What should I focus on first?" in call_kwargs["messages"][0]["content"][0]["text"]
    assert call_kwargs["inferenceConfig"]["maxTokens"] == 500


def test_compose_handles_multiple_text_content_blocks():
    """The Converse API's content is a list of blocks; concatenate every
    text block rather than assuming exactly one."""
    client = MagicMock()
    client.converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": "Part one. "}, {"text": "Part two."}],
            }
        }
    }
    with patch("boto3.client", return_value=client):
        narrator = BedrockNarrator()

    from care_agent.models import UserProfile
    from care_agent.reasoning import Brief

    result = narrator.compose(
        Brief(intent="general_bloodwork_question"),
        "hi",
        UserProfile(user_id="u1", display_name="Alex", age=42, sex="female", country="PT"),
    )
    assert result == "Part one. Part two."


def test_default_model_from_env(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "anthropic.claude-opus-5")
    with patch("boto3.client", return_value=MagicMock()):
        narrator = BedrockNarrator()
    assert narrator._model_id == "anthropic.claude-opus-5"


def test_explicit_model_overrides_env(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "anthropic.claude-opus-5")
    with patch("boto3.client", return_value=MagicMock()):
        narrator = BedrockNarrator(model_id="anthropic.claude-haiku-4-5-20251001-v1:0")
    assert narrator._model_id == "anthropic.claude-haiku-4-5-20251001-v1:0"


def test_default_region_from_env(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("BEDROCK_REGION", "us-west-2")
    with patch("boto3.client", return_value=MagicMock()) as mock_client:
        BedrockNarrator()
    mock_client.assert_called_once_with("bedrock-runtime", region_name="us-west-2")


def test_explicit_region_overrides_env(monkeypatch):
    monkeypatch.setenv("BEDROCK_REGION", "us-west-2")
    with patch("boto3.client", return_value=MagicMock()) as mock_client:
        BedrockNarrator(region="eu-west-1")
    mock_client.assert_called_once_with("bedrock-runtime", region_name="eu-west-1")


def test_agent_with_bedrock_narrator_passes_through_when_grounded(data_dir):
    """Full pipeline, real BedrockNarrator class, boto3 client mocked: a
    faithful rephrasing (same numbers, no new claims) should pass safety
    checks and be returned as-is -- narrator_backend='bedrock', no fallback.

    This is Phase 4's core test: the same safety pipeline (no_diagnosis,
    no_dosing, numeric_grounding) that already guards Anthropic/OpenAI/
    Google/Ollama output, unchanged, applied to Bedrock's Converse API
    response shape for the first time.
    """
    from care_agent.agent import HealthAgent

    fake_client = _fake_bedrock_client(
        "Your LDL-C came in at 162 mg/dL and HbA1c at 6.1%, both flagged. "
        "Given your knee pain, low-impact movement like walking or cycling "
        "is a better fit than running. Worth discussing with your clinician."
    )

    with patch("boto3.client", return_value=fake_client):
        narrator = BedrockNarrator(model_id="anthropic.claude-haiku-4-5-20251001-v1:0")

    agent = HealthAgent(
        data_dir=data_dir,
        catalog_path=data_dir / "mock_biomarker_catalog.sqlite",
        kb_path=data_dir / "knowledge_base.jsonl",
        narrator=narrator,
    )

    response = agent.ask(user_id="user_demo_001", question_text="What should I focus on first?")

    assert response.safe is True
    assert response.trace.narrator_backend == "bedrock"
    assert "162" in response.answer
    assert "6.1" in response.answer
    fallback_checks = [c for c in response.trace.safety_checks if c.name == "narrator_fallback"]
    assert fallback_checks == []


def test_agent_with_bedrock_narrator_falls_back_when_unsafe(data_dir):
    """If Bedrock's model hallucinates a diagnosis/dose/number, the agent
    must still return the safe, deterministic mock-narrator answer -- same
    guardrail, unmodified, now proven against a fourth cloud model style."""
    from care_agent.agent import HealthAgent

    fake_client = _fake_bedrock_client("You definitely have type 2 diabetes. Take 500 mg of metformin twice daily.")

    with patch("boto3.client", return_value=fake_client):
        narrator = BedrockNarrator(model_id="anthropic.claude-haiku-4-5-20251001-v1:0")

    agent = HealthAgent(
        data_dir=data_dir,
        catalog_path=data_dir / "mock_biomarker_catalog.sqlite",
        kb_path=data_dir / "knowledge_base.jsonl",
        narrator=narrator,
    )

    response = agent.ask(user_id="user_demo_001", question_text="What should I focus on first?")

    assert response.safe is True
    assert "you definitely have type 2 diabetes" not in response.answer.lower()
    assert "500 mg" not in response.answer.lower()
    fallback_checks = [c for c in response.trace.safety_checks if c.name == "narrator_fallback"]
    assert len(fallback_checks) == 1
    # Regression: narrator_backend used to stay "bedrock" even after a
    # fallback (it's set once, before the fallback decision, and was
    # never corrected) -- a consumer reading only this field, not also
    # checking for the narrator_fallback entry above, would wrongly
    # conclude Bedrock's own output was returned. See docs/DECISIONS.md.
    assert response.trace.narrator_backend == "mock"
    # Requested after testing the Workbench: a fallback was visible but
    # opaque -- no way to see what the rejected draft said or specifically
    # why. The rejected draft and the failing check names/details must now
    # both be recoverable from the trace.
    assert response.trace.rejected_draft is not None
    assert "type 2 diabetes" in response.trace.rejected_draft.lower()
    # This specific fake draft's failures: "take 500 mg" (no_dosing) and
    # 500 being an ungrounded number (numeric_grounding). Not no_diagnosis
    # -- "you definitely have type 2 diabetes" doesn't match the
    # diagnosis pattern's `you (have|are)` shape because of the
    # intervening "definitely" (a real, separate gap, not this test's
    # concern).
    assert "no_dosing" in fallback_checks[0].detail
    assert "numeric_grounding" in fallback_checks[0].detail


def test_agent_with_bedrock_narrator_does_not_fall_back_for_a_number_echoed_from_the_question(data_dir):
    """Regression test: found live-testing the Workbench. Bedrock correctly
    declined to calculate a "10-year cardiovascular risk score" (not
    something this project's data supports), but the safest possible
    response -- explicitly refusing to fabricate a number -- used to fail
    grounding anyway, purely because "10" (echoed from the user's own
    "10-year" phrasing) matched no grounded fact. This must now pass
    without falling back to the (objectively less helpful, in this case)
    mock template."""
    from care_agent.agent import HealthAgent

    fake_client = _fake_bedrock_client(
        "I can't calculate your 10-year cardiovascular risk score -- that requires a clinical assessment by your "
        "healthcare provider using a validated tool, along with additional information like your age and blood "
        "pressure. That said, your LDL-C is 162 mg/dL and HbA1c is 6.1%, both flagged in your latest panel."
    )

    with patch("boto3.client", return_value=fake_client):
        narrator = BedrockNarrator(model_id="anthropic.claude-haiku-4-5-20251001-v1:0")

    agent = HealthAgent(
        data_dir=data_dir,
        catalog_path=data_dir / "mock_biomarker_catalog.sqlite",
        kb_path=data_dir / "knowledge_base.jsonl",
        narrator=narrator,
    )

    response = agent.ask(
        user_id="user_demo_001", question_text="Can you calculate my 10-year cardiovascular risk score from these results?"
    )

    assert response.safe is True
    assert response.trace.narrator_backend == "bedrock"
    fallback_checks = [c for c in response.trace.safety_checks if c.name == "narrator_fallback"]
    assert fallback_checks == []


def test_no_content_blocks_returns_empty_string():
    """Defensive: an unexpected/empty response shape shouldn't crash --
    it should just produce empty text, which the numeric-grounding safety
    check would reject as an unsafe/empty answer (handled by agent.py's
    fallback), not something this narrator needs to guard against itself."""
    client = MagicMock()
    client.converse.return_value = {"output": {"message": {"role": "assistant", "content": []}}}
    with patch("boto3.client", return_value=client):
        narrator = BedrockNarrator()

    from care_agent.models import UserProfile
    from care_agent.reasoning import Brief

    result = narrator.compose(
        Brief(intent="general_bloodwork_question"),
        "hi",
        UserProfile(user_id="u1", display_name="Alex", age=42, sex="female", country="PT"),
    )
    assert result == ""
