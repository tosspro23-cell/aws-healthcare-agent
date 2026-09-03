"""Unit tests for the optional OpenAI narrator.

Skipped entirely (via ``importorskip``) unless the ``openai`` package is
installed -- CI's default `pip install -e ".[dev]"` does not install it, so
these tests never run there. The SDK client itself is mocked, so no network
call or API key is ever needed even when the package is present.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("openai")

from care_agent.narrator.openai_narrator import OpenAINarrator  # noqa: E402


def _fake_openai_client(reply_text: str) -> MagicMock:
    client = MagicMock()
    message = MagicMock()
    message.content = reply_text
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    client.chat.completions.create.return_value = response
    return client


def test_missing_api_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAINarrator()


def test_compose_sends_grounded_text_and_returns_reply(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    fake_client = _fake_openai_client("Rephrased but still grounded answer with LDL-C 162 mg/dL.")

    with patch("openai.OpenAI", return_value=fake_client):
        narrator = OpenAINarrator(model="gpt-4o-mini")

    from care_agent.models import UserProfile
    from care_agent.reasoning import Brief

    brief = Brief(intent="general_bloodwork_question")
    profile = UserProfile(user_id="u1", display_name="Alex", age=42, sex="female", country="PT")

    result = narrator.compose(brief, "What should I focus on first?", profile)

    assert result == "Rephrased but still grounded answer with LDL-C 162 mg/dL."
    call_kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["messages"][0]["role"] == "system"
    assert "What should I focus on first?" in call_kwargs["messages"][1]["content"]


def test_default_model_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    with patch("openai.OpenAI", return_value=MagicMock()):
        narrator = OpenAINarrator()
    assert narrator._model == "gpt-4o"


def test_explicit_model_overrides_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    with patch("openai.OpenAI", return_value=MagicMock()):
        narrator = OpenAINarrator(model="gpt-4o-mini")
    assert narrator._model == "gpt-4o-mini"
