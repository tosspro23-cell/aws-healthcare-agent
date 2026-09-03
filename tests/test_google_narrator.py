"""Unit tests for the optional Google Gemini narrator.

Skipped entirely (via ``importorskip``) unless the ``google-genai`` package
is installed -- CI's default `pip install -e ".[dev]"` does not install it,
so these tests never run there. The SDK client itself is mocked, so no
network call or API key is ever needed even when the package is present.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("google.genai")

from care_agent.narrator.google_narrator import GoogleNarrator  # noqa: E402


def _fake_genai_client(reply_text: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.text = reply_text
    client.models.generate_content.return_value = response
    return client


def test_missing_api_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        GoogleNarrator()


def test_compose_sends_grounded_text_and_returns_reply(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-not-real")
    fake_client = _fake_genai_client("Rephrased but still grounded answer with LDL-C 162 mg/dL.")

    with patch("google.genai.Client", return_value=fake_client):
        narrator = GoogleNarrator(model="gemini-2.0-flash")

    from care_agent.models import UserProfile
    from care_agent.reasoning import Brief

    brief = Brief(intent="general_bloodwork_question")
    profile = UserProfile(user_id="u1", display_name="Alex", age=42, sex="female", country="PT")

    result = narrator.compose(brief, "What should I focus on first?", profile)

    assert result == "Rephrased but still grounded answer with LDL-C 162 mg/dL."
    call_kwargs = fake_client.models.generate_content.call_args.kwargs
    assert call_kwargs["model"] == "gemini-2.0-flash"
    assert "What should I focus on first?" in call_kwargs["contents"]
    assert call_kwargs["config"].system_instruction is not None


def test_accepts_gemini_api_key_env_var(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-real")
    with patch("google.genai.Client", return_value=MagicMock()):
        narrator = GoogleNarrator()
    assert narrator is not None


def test_default_model_from_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-not-real")
    monkeypatch.setenv("GOOGLE_MODEL", "gemini-2.5-flash")
    with patch("google.genai.Client", return_value=MagicMock()):
        narrator = GoogleNarrator()
    assert narrator._model == "gemini-2.5-flash"
