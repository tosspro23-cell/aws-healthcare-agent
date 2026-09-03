"""Optional Amazon Bedrock-backed narrator.

Off by default. Enable with ``CARE_AGENT_NARRATOR_BACKEND=bedrock``;
otherwise the agent always falls back to ``MockNarrator``. Nothing in this
file is required for this project's "no paid API" goal or for tests.

Unlike the other cloud narrators (Anthropic/OpenAI/Google), this one has no
separate API-key env var: Bedrock authenticates via the standard AWS
credential chain (an `AWS_PROFILE`, an IAM role, etc. -- whatever `aws
configure` or the runtime environment already provides). `boto3` is the
only dependency, and it's already present anywhere this repo's Lambdas run
(it ships in the Lambda runtime image) or where `docs/AWS_SETUP.md` was
followed locally.

IAM: the caller needs `bedrock:InvokeModel` scoped to the specific model
(or inference-profile) ARN being used -- see `infra/stacks/orchestration_stack.py`
or wherever this backend is wired into a deployed Lambda for the actual
grant. No broad `bedrock:*` permission is required or should be granted.

Same safety contract as every other narrator: only the mock narrator's
already-grounded bullet list is sent to the model -- never raw dataset
JSON -- and `agent.py` re-verifies the returned text with
`safety.run_safety_checks`, falling back to the mock narrator if it fails
any check. This is the whole point of Phase 4's test: does that same
safety net hold up against a model with a materially different output
style, without having been rewritten for it?
"""

from __future__ import annotations

import os

from care_agent.models import UserProfile
from care_agent.narrator._prompt import SYSTEM_PROMPT
from care_agent.narrator.mock_narrator import MockNarrator
from care_agent.reasoning import Brief

# Newer Anthropic models on Bedrock require a cross-region inference
# profile ID (the "us." prefix), not the bare on-demand model ID --
# confirmed by a live call: the bare ID fails with "Invocation of model ID
# ... with on-demand throughput isn't supported," and the "us."-prefixed
# ID succeeds. If a future/different model needs a different prefix (or
# none), override via BEDROCK_MODEL_ID rather than editing this default.
DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_REGION = "us-east-1"


class BedrockNarrator:
    """Thin wrapper around Bedrock's Converse API for prose polishing only."""

    backend_name = "bedrock"

    def __init__(self, model_id: str | None = None, region: str | None = None):
        try:
            import boto3  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only when extra installed
            raise RuntimeError(
                "The 'boto3' package is not installed. Install with `pip install .[bedrock]` "
                "or unset CARE_AGENT_NARRATOR_BACKEND to use the mock narrator."
            ) from exc

        self._region = region or os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION", DEFAULT_REGION)
        self._client = boto3.client("bedrock-runtime", region_name=self._region)
        self._model_id = model_id or os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
        self._mock = MockNarrator()

    def compose(self, brief: Brief, question_text: str, profile: UserProfile) -> str:
        grounded_text = self._mock.compose(brief, question_text, profile)

        response = self._client.converse(
            modelId=self._model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                f"User's question: {question_text}\n\n"
                                f"Grounded facts and constraints to rephrase (do not add to this list):\n{grounded_text}"
                            )
                        }
                    ],
                }
            ],
            system=[{"text": SYSTEM_PROMPT}],
            inferenceConfig={"maxTokens": 500},
        )

        content_blocks = response.get("output", {}).get("message", {}).get("content", [])
        text_parts = [block["text"] for block in content_blocks if "text" in block]
        return "".join(text_parts).strip()
