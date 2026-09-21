"""Tests for `tool_planner._clean_tool_args` -- the pure argument-cleaning
function extracted from `BedrockToolPlanner.propose_plan`. No `boto3`
dependency, unlike the class itself, so this runs without the `bedrock`
extra installed (this module previously had zero test coverage at all).
"""

from __future__ import annotations

from care_agent.orchestrator import PlannedToolCall, capability_gate
from care_agent.tool_planner import _clean_tool_args


def test_clean_tool_args_coerces_real_scalars_to_strings():
    assert _clean_tool_args({"concept_id": "ldl_c_mg_dl", "count": 3, "ratio": 1.5, "flag": True}) == {
        "concept_id": "ldl_c_mg_dl",
        "count": "3",
        "ratio": "1.5",
        "flag": "True",
    }


def test_clean_tool_args_drops_none_values():
    """Regression test: an independent review found `str(None)` produces
    the non-empty string "None", which fooled the required-argument
    presence check added for a separate finding (F4) -- a missing/null
    argument must read as genuinely absent, not as a suspiciously
    literal "None"."""
    assert _clean_tool_args({"field": None}) == {}


def test_clean_tool_args_drops_list_and_dict_values():
    assert _clean_tool_args({"field": [], "other": {}}) == {}


def test_clean_tool_args_drops_only_the_bad_keys_not_the_whole_call():
    assert _clean_tool_args({"concept_id": "ldl_c_mg_dl", "junk": None}) == {"concept_id": "ldl_c_mg_dl"}


def test_a_null_argument_is_correctly_rejected_by_capability_gate():
    """End-to-end proof this actually closes the loophole: a tool call
    whose only argument was `None` (as `BedrockToolPlanner` would produce
    it) is now rejected as missing, not accepted as a real (if odd)
    string value."""
    call = PlannedToolCall("get_questionnaire_fact", _clean_tool_args({"field": None}))
    allowed, reason = capability_gate(call)
    assert allowed is False
    assert "field" in reason
