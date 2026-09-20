"""Optional Amazon Bedrock-backed `ToolPlanner` (see `orchestrator.py`).

Off by default -- `ask_compound()` requires an explicit `planner` argument
(there is no safe deterministic default the way `MockNarrator` is for
narration: no fixed template can plan an open-ended tool combination, so
unlike every other backend-selection factory in this project there is
nothing to silently fall back to here). Enable this one by passing
`BedrockToolPlanner()` explicitly.

Same auth/IAM model as `narrator/bedrock_narrator.py`: standard AWS
credential chain, `bedrock:InvokeModel` scoped to the specific model ARN.
Uses the Converse API's `toolConfig` (tool use / function calling) --
verified live-documented support for the Claude Haiku 4.5 model this
project's `bedrock_narrator.py` already defaults to (see
`docs/DECISIONS.md`, 2026-09-20 entry, for the sourcing) -- not a new
model dependency, the same one already in use for narration.
"""

from __future__ import annotations

import json
import os

from care_agent.orchestrator import TOOL_SPECS, PlannedToolCall, ToolPlan, planner_prompt

DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_REGION = "us-east-1"


def _tool_config() -> dict:
    """`TOOL_SPECS`' lightweight parameter dicts, translated into Bedrock
    Converse's `toolConfig.tools[].toolSpec.inputSchema.json` shape (a real
    JSON Schema object) -- kept as a translation step, not authored twice,
    so the tool vocabulary the planner is told about can never drift from
    `orchestrator.capability_gate`'s own enforcement of it."""
    tools = []
    for spec in TOOL_SPECS:
        properties = {name: {k: v for k, v in prop.items() if k != "required"} for name, prop in spec["parameters"].items()}
        required = [name for name, prop in spec["parameters"].items() if prop.get("required", True)]
        tools.append(
            {
                "toolSpec": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "inputSchema": {"json": {"type": "object", "properties": properties, "required": required}},
                }
            }
        )
    return {"tools": tools}


class BedrockToolPlanner:
    """Thin wrapper around Bedrock's Converse API tool-use, scoped to this
    project's fixed `TOOL_SPECS` vocabulary only."""

    backend_name = "bedrock"

    def __init__(self, model_id: str | None = None, region: str | None = None):
        try:
            import boto3  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only when extra installed
            raise RuntimeError(
                "The 'boto3' package is not installed. Install with `pip install .[bedrock]` to use BedrockToolPlanner."
            ) from exc

        self._region = region or os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION", DEFAULT_REGION)
        self._client = boto3.client("bedrock-runtime", region_name=self._region)
        self._model_id = model_id or os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
        self._tool_config = _tool_config()

    def propose_plan(self, question_text: str, repair_reason: str | None = None) -> ToolPlan:
        prompt = planner_prompt(question_text, repair_reason)
        response = self._client.converse(
            modelId=self._model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            toolConfig=self._tool_config,
            inferenceConfig={"maxTokens": 500},
        )
        content_blocks = response.get("output", {}).get("message", {}).get("content", [])
        calls = []
        for block in content_blocks:
            tool_use = block.get("toolUse")
            if not tool_use:
                continue
            raw_args = tool_use.get("input", {})
            # Converse returns `input` already parsed as a dict for a
            # well-formed tool call; guard against a stringified-JSON
            # response some models/edge cases produce rather than assume
            # the shape.
            if isinstance(raw_args, str):
                try:
                    raw_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    raw_args = {}
            calls.append(PlannedToolCall(tool_name=tool_use.get("name", ""), args={k: str(v) for k, v in raw_args.items()}))
        return ToolPlan(calls=tuple(calls))
