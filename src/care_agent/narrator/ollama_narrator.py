"""Optional local-model narrator, backed by a locally running Ollama server.

Off by default. Enable with ``CARE_AGENT_NARRATOR_BACKEND=ollama`` once Ollama
(https://ollama.com) is installed, `ollama serve` is running, and a model has
been pulled (e.g. ``ollama pull llama3.1``). This is the "use an open-source
or local LLM if you want" path the project brief explicitly allows, and it
needs zero pip installs (stdlib ``urllib`` only), no API key, no cost, and no
data leaving the machine -- everything stays on localhost.

Same safety contract as ``AnthropicNarrator``: it only rephrases the mock
narrator's already-grounded bullet list -- the model never sees raw dataset
JSON -- and ``agent.py`` re-verifies its output with the same
diagnosis/dosing/numeric-grounding checks, falling back to the mock narrator
if it fails any of them.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from care_agent.models import UserProfile
from care_agent.narrator._prompt import SYSTEM_PROMPT
from care_agent.narrator.mock_narrator import MockNarrator
from care_agent.reasoning import Brief

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1"


def _extract_message_text(response_body: dict) -> str:
    message = response_body.get("message", {})
    return (message.get("content") or "").strip()


class OllamaNarrator:
    """Rephrases the grounded answer using a locally running Ollama model."""

    backend_name = "ollama"

    def __init__(self, model: str | None = None, host: str | None = None, timeout: float = 60.0):
        self._model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        self._host = (host or os.environ.get("OLLAMA_HOST", DEFAULT_HOST)).rstrip("/")
        self._timeout = timeout
        self._mock = MockNarrator()

    def compose(self, brief: Brief, question_text: str, profile: UserProfile) -> str:
        # Same pattern as AnthropicNarrator: the model only ever sees the
        # already-grounded bullet list, never the raw dataset.
        grounded_text = self._mock.compose(brief, question_text, profile)

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"User's question: {question_text}\n\n"
                        f"Grounded facts and constraints to rephrase (do not add to this list):\n{grounded_text}"
                    ),
                },
            ],
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self._host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Could not reach Ollama at {self._host} (model={self._model!r}). "
                f"Is `ollama serve` running, and has the model been pulled "
                f"(`ollama pull {self._model}`)? Original error: {exc}"
            ) from exc

        return _extract_message_text(body)
