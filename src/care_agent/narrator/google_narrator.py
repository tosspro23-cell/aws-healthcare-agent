"""Optional Google Gemini-backed narrator.

Off by default. Enable with ``CARE_AGENT_NARRATOR_BACKEND=google`` and
``GOOGLE_API_KEY`` (or ``GEMINI_API_KEY``) set; otherwise the agent always
falls back to ``MockNarrator``. Nothing in this file is required for this
project's "no paid API" goal or for tests -- Gemini has a free usage tier,
but this backend is still fully optional.

Same safety contract as the other cloud/local narrators: only the mock
narrator's already-grounded bullet list is sent to the model, and
``agent.py`` re-verifies the returned text, falling back to the mock
narrator if it fails a safety check.

Uses the ``google-genai`` SDK (``pip install google-genai``, imported as
``from google import genai``). If Google renames or reshapes this SDK after
this was written, construction/compose will raise a clear ``RuntimeError``
rather than fail silently or crash somewhere unrelated.
"""

from __future__ import annotations

import os

from care_agent.models import UserProfile
from care_agent.narrator._prompt import SYSTEM_PROMPT
from care_agent.narrator.mock_narrator import MockNarrator
from care_agent.reasoning import Brief

DEFAULT_MODEL = "gemini-2.0-flash"


class GoogleNarrator:
    """Thin wrapper around the Gemini API for prose polishing only."""

    backend_name = "google"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        try:
            from google import genai  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only when extra installed
            raise RuntimeError(
                "The 'google-genai' package is not installed. Install with `pip install .[google]` "
                "or unset CARE_AGENT_NARRATOR_BACKEND to use the mock narrator."
            ) from exc

        key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GOOGLE_API_KEY (or GEMINI_API_KEY) is not set; cannot use the google narrator backend.")

        self._genai = genai
        self._client = genai.Client(api_key=key)
        self._model = model or os.environ.get("GOOGLE_MODEL", DEFAULT_MODEL)
        self._mock = MockNarrator()

    def compose(self, brief: Brief, question_text: str, profile: UserProfile) -> str:
        grounded_text = self._mock.compose(brief, question_text, profile)

        user_content = (
            f"User's question: {question_text}\n\nGrounded facts and constraints to rephrase (do not add to this list):\n{grounded_text}"
        )

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=user_content,
                config=self._genai.types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    max_output_tokens=500,
                ),
            )
        except AttributeError as exc:  # pragma: no cover - depends on installed SDK shape
            raise RuntimeError(
                "The installed google-genai SDK does not match the expected API shape "
                "(client.models.generate_content / genai.types.GenerateContentConfig). "
                f"Original error: {exc}"
            ) from exc

        return (response.text or "").strip()
