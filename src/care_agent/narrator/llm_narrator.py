"""Optional LLM narrator.

Off by default. Enable with ``CARE_AGENT_NARRATOR_BACKEND=anthropic`` and
``ANTHROPIC_API_KEY`` set; otherwise the agent always falls back to
``MockNarrator`` (see ``agent.py::_select_narrator``), so nothing in this
file is required for this project's "no paid API" goal or for tests.

Safety design: the LLM is given the same grounded bullet points the mock
narrator uses -- never raw JSON, never the full dataset -- and is instructed
to rephrase only, not add numbers or claims. That instruction is a courtesy,
not the guarantee: the real guarantee is that ``agent.py`` re-runs
``safety.run_safety_checks`` (including numeric-grounding verification) on
whatever text comes back, LLM or not, and falls back to the mock narrator's
output if the LLM output fails any check.
"""

from __future__ import annotations

import os

from care_agent.models import UserProfile
from care_agent.narrator._prompt import SYSTEM_PROMPT
from care_agent.narrator.mock_narrator import MockNarrator
from care_agent.reasoning import Brief


class AnthropicNarrator:
    """Thin wrapper around the Anthropic Messages API for prose polishing only."""

    backend_name = "anthropic"

    def __init__(self, model: str = "claude-sonnet-5", api_key: str | None = None):
        try:
            import anthropic  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only when extra installed
            raise RuntimeError(
                "The 'anthropic' package is not installed. Install with `pip install .[llm]` "
                "or unset CARE_AGENT_NARRATOR_BACKEND to use the mock narrator."
            ) from exc

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set; cannot use the anthropic narrator backend.")

        self._client = anthropic.Anthropic(api_key=key)
        self._model = model
        self._mock = MockNarrator()

    def compose(self, brief: Brief, question_text: str, profile: UserProfile) -> str:
        # Reuse the mock narrator to build the same grounded bullet list, then
        # ask the model to rephrase it -- this way the LLM never sees raw
        # dataset JSON and cannot introduce facts the deterministic core
        # didn't already verify.
        grounded_text = self._mock.compose(brief, question_text, profile)

        message = self._client.messages.create(
            model=self._model,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"User's question: {question_text}\n\n"
                        f"Grounded facts and constraints to rephrase (do not add to this list):\n{grounded_text}"
                    ),
                }
            ],
        )
        # message.content can include non-text blocks (tool use, thinking, ...);
        # getattr rather than block.text keeps this safe for mypy and for any
        # future response shape without an isinstance import per block type.
        text_parts = [getattr(block, "text", "") for block in message.content if getattr(block, "type", "") == "text"]
        return "".join(text_parts).strip()
