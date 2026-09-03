"""Unit tests for the optional Ollama narrator.

These never hit a real Ollama server -- ``urllib.request.urlopen`` is
patched, so this suite runs identically whether or not Ollama is installed.
Live, end-to-end verification against a real local Ollama server is a manual
step (see README), not something CI can depend on.
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch

from care_agent.narrator.ollama_narrator import OllamaNarrator, _extract_message_text


def _fake_response(text: str) -> io.BytesIO:
    payload = {"message": {"role": "assistant", "content": text}}
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


class _FakeHTTPResponse:
    def __init__(self, text: str):
        self._buf = _fake_response(text)

    def read(self):
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_extract_message_text_happy_path():
    body = {"message": {"role": "assistant", "content": "Hello there."}}
    assert _extract_message_text(body) == "Hello there."


def test_extract_message_text_missing_message_returns_empty():
    assert _extract_message_text({}) == ""


def test_compose_calls_ollama_chat_endpoint_and_parses_reply():
    from care_agent.models import UserProfile
    from care_agent.reasoning import Brief

    narrator = OllamaNarrator(model="llama3.1", host="http://localhost:11434")
    brief = Brief(intent="general_bloodwork_question")
    profile = UserProfile(user_id="u1", display_name="Alex", age=42, sex="female", country="PT")

    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse("Rephrased but still grounded answer.")

    with patch("care_agent.narrator.ollama_narrator.urllib.request.urlopen", fake_urlopen):
        result = narrator.compose(brief, "What should I focus on first?", profile)

    assert result == "Rephrased but still grounded answer."
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["body"]["model"] == "llama3.1"
    assert captured["body"]["stream"] is False
    assert "What should I focus on first?" in captured["body"]["messages"][1]["content"]


def test_compose_raises_clear_error_when_server_unreachable():
    import urllib.error

    from care_agent.models import UserProfile
    from care_agent.reasoning import Brief

    narrator = OllamaNarrator(model="llama3.1", host="http://localhost:1")  # nothing listens here
    brief = Brief(intent="general_bloodwork_question")
    profile = UserProfile(user_id="u1", display_name="Alex", age=42, sex="female", country="PT")

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    with patch("care_agent.narrator.ollama_narrator.urllib.request.urlopen", fake_urlopen):
        try:
            narrator.compose(brief, "hello", profile)
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "Could not reach Ollama" in str(exc)
            assert "ollama pull" in str(exc)


def test_default_model_and_host_from_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:4b")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:9999")
    narrator = OllamaNarrator()
    assert narrator._model == "qwen3:4b"
    assert narrator._host == "http://localhost:9999"


def test_explicit_args_override_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:4b")
    narrator = OllamaNarrator(model="phi3")
    assert narrator._model == "phi3"


def test_agent_with_ollama_narrator_passes_through_when_grounded(data_dir):
    """Full pipeline, real OllamaNarrator class, HTTP layer mocked: a
    faithful rephrasing (same numbers, no new claims) should pass safety
    checks and be returned as-is -- narrator_backend='ollama', no fallback.
    """
    from care_agent.agent import HealthAgent

    def fake_urlopen(request, timeout=None):
        # A plausible local-model rephrasing that keeps every real number.
        text = (
            "Your LDL-C came in at 162 mg/dL and HbA1c at 6.1%, both flagged. "
            "Given your knee pain, low-impact movement like walking or cycling "
            "is a better fit than running. Worth discussing with your clinician."
        )
        return _FakeHTTPResponse(text)

    agent = HealthAgent(
        data_dir=data_dir,
        catalog_path=data_dir / "mock_biomarker_catalog.sqlite",
        kb_path=data_dir / "knowledge_base.jsonl",
        narrator=OllamaNarrator(model="llama3.1", host="http://localhost:11434"),
    )

    with patch("care_agent.narrator.ollama_narrator.urllib.request.urlopen", fake_urlopen):
        response = agent.ask(user_id="user_demo_001", question_text="What should I focus on first?")

    assert response.safe is True
    assert response.trace.narrator_backend == "ollama"
    assert "162" in response.answer
    assert "6.1" in response.answer
    fallback_checks = [c for c in response.trace.safety_checks if c.name == "narrator_fallback"]
    assert fallback_checks == []


def test_agent_with_ollama_narrator_falls_back_when_unsafe(data_dir):
    """If the local model hallucinates a diagnosis/dose/number, the agent
    must still return the safe, deterministic mock-narrator answer.
    """
    from care_agent.agent import HealthAgent

    def fake_urlopen(request, timeout=None):
        text = "You definitely have type 2 diabetes. Take 500 mg of metformin twice daily."
        return _FakeHTTPResponse(text)

    agent = HealthAgent(
        data_dir=data_dir,
        catalog_path=data_dir / "mock_biomarker_catalog.sqlite",
        kb_path=data_dir / "knowledge_base.jsonl",
        narrator=OllamaNarrator(model="llama3.1", host="http://localhost:11434"),
    )

    with patch("care_agent.narrator.ollama_narrator.urllib.request.urlopen", fake_urlopen):
        response = agent.ask(user_id="user_demo_001", question_text="What should I focus on first?")

    assert response.safe is True
    assert "you definitely have type 2 diabetes" not in response.answer.lower()
    assert "500 mg" not in response.answer.lower()
    fallback_checks = [c for c in response.trace.safety_checks if c.name == "narrator_fallback"]
    assert len(fallback_checks) == 1
