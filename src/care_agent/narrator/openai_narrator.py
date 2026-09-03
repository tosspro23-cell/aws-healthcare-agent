"""Optional OpenAI-backed narrator.

Off by default. Enable with ``CARE_AGENT_NARRATOR_BACKEND=openai`` and
``OPENAI_API_KEY`` set; otherwise the agent always falls back to
``MockNarrator``. Nothing in this file is required for this project's
"no paid API" goal or for tests.

Same safety contract as the Anthropic/Ollama narrators: only the mock
narrator's already-grounded bullet list is sent to the model -- never raw
dataset JSON -- and ``agent.py`` re-verifies the returned text with
``safety.run_safety_checks``, falling back to the mock narrator if it fails
any check.
"""

from __future__ import annotations

import os

from care_agent.models import UserProfile
from care_agent.narrator._prompt import SYSTEM_PROMPT
from care_agent.narrator.mock_narrator import MockNarrator
from care_agent.reasoning import Brief

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAINarrator:
    """Thin wrapper around the OpenAI Chat Completions API for prose polishing only."""

    backend_name = "openai"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        try:
            import openai  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only when extra installed
            raise RuntimeError(
                "The 'openai' package is not installed. Install with `pip install .[openai]` "
                "or unset CARE_AGENT_NARRATOR_BACKEND to use the mock narrator."
            ) from exc

        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set; cannot use the openai narrator backend.")

        self._client = openai.OpenAI(api_key=key)
        self._model = model or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
        self._mock = MockNarrator()

    def compose(self, brief: Brief, question_text: str, profile: UserProfile) -> str:
        grounded_text = self._mock.compose(brief, question_text, profile)

        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=500,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"User's question: {question_text}\n\n"
                        f"Grounded facts and constraints to rephrase (do not add to this list):\n{grounded_text}"
                    ),
                },
            ],
        )
        return (response.choices[0].message.content or "").strip()
