"""V1/V2 path-equivalence tests for source-data staleness/plausibility
checks. Both `HealthAgent.ask()` (V1) and `HealthAgent.ask_compound()`
(V2) now call the same `_apply_source_data_checks` (see
docs/DECISIONS.md, 2026-09-21 entry -- an independent review found V2
never ran either check at all before that extraction). This file proves
the two pipelines actually stay in sync against the same injected bad
data, reusing the exact scenarios `tests/test_agent_edge_cases.py`
already established for V1 alone.
"""

from __future__ import annotations

from care_agent.agent import HealthAgent
from care_agent.orchestrator import PlannedToolCall, ToolPlan


def _agent_for(path):
    return HealthAgent(data_dir=path, catalog_path=path / "mock_biomarker_catalog.sqlite", kb_path=path / "knowledge_base.jsonl")


class _MarkerSnapshotPlanner:
    """Always asks for one specific marker's snapshot -- a minimal
    scripted planner, same pattern as tests/test_orchestrator.py's
    `_ScriptedPlanner`, kept local rather than imported across test
    files."""

    backend_name = "fake"

    def __init__(self, concept_id: str):
        self._concept_id = concept_id

    def propose_plan(self, question_text: str, repair_reason: str | None = None) -> ToolPlan:
        return ToolPlan(calls=(PlannedToolCall("get_marker_snapshot", {"concept_id": self._concept_id}),))


# Same shape as test_agent_edge_cases.py's test_stale_panel_flags_limitation.
_STALE_BLOODWORK = {
    "user_id": "user_demo_001",
    "dataset_version": "v1",
    "latest_panel": {
        "panel_id": "panel_old",
        "measurement_date": "2019-01-01",
        "overall_flags": [],
        "biomarkers": [
            {
                "concept_id": "ldl_c_mg_dl",
                "display_name": "LDL-C",
                "value": 162,
                "unit": "mg/dL",
                "classification": "high",
                "action_fields": ["NUTRITION"],
            }
        ],
    },
    "previous_panels": [],
}

# Same shape as test_agent_edge_cases.py's test_implausible_value_is_flagged_as_a_limitation.
_IMPLAUSIBLE_BLOODWORK = {
    "user_id": "user_demo_001",
    "dataset_version": "v1",
    "latest_panel": {
        "panel_id": "panel_implausible",
        "measurement_date": "2026-05-06",
        "overall_flags": [],
        "biomarkers": [
            {
                # A value no living human could have -- almost certainly a
                # data entry or unit error, distinct from "severe but real".
                "concept_id": "ldl_c_mg_dl",
                "display_name": "LDL-C",
                "value": 50000,
                "unit": "mg/dL",
                "classification": "high",
                "action_fields": ["MEDICAL"],
            }
        ],
    },
    "previous_panels": [],
}


def test_v1_and_v2_both_flag_a_stale_panel(dataset_builder):
    path = dataset_builder(bloodwork=_STALE_BLOODWORK)
    agent = _agent_for(path)

    v1_response = agent.ask(user_id="user_demo_001", question_text="What should I focus on first?")
    v2_response = agent.ask_compound(
        user_id="user_demo_001", question_text="What is my LDL level?", planner=_MarkerSnapshotPlanner("ldl_c_mg_dl")
    )

    assert "stale_data" in {lim.kind for lim in v1_response.trace.limitations}
    assert "stale_data" in {lim.kind for lim in v2_response.trace.limitations}
    assert v1_response.safe is True
    assert v2_response.safe is True


def test_v1_and_v2_both_flag_an_implausible_value(dataset_builder):
    path = dataset_builder(bloodwork=_IMPLAUSIBLE_BLOODWORK)
    agent = _agent_for(path)

    v1_response = agent.ask(user_id="user_demo_001", question_text="What should I focus on first?")
    v2_response = agent.ask_compound(
        user_id="user_demo_001", question_text="What is my LDL level?", planner=_MarkerSnapshotPlanner("ldl_c_mg_dl")
    )

    assert "implausible_value" in {lim.kind for lim in v1_response.trace.limitations}
    assert "implausible_value" in {lim.kind for lim in v2_response.trace.limitations}
    assert v1_response.safe is True
    assert v2_response.safe is True
