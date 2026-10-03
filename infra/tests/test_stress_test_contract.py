"""Contract test for `infra/scripts/stress_test.py`'s direct Step
Functions calls (`burst-async`, `race`): those calls bypass `start_run.py`
(and its own validation/defaulting) entirely, building their own
execution input directly. An independent review found `engine`/
`prior_run_ids` missing from that input after both were added to
`InvokeAgent`'s own `Payload` whitelist in `orchestration_stack.py` --
undetected by anything in CI, since `stress_test.py` is a live-only
harness, not part of the pytest suite it runs in. This test closes that
gap at the root: it diffs `stress_test.py`'s own input-builder output
against the real synthesized ASL, so a future required field that's
added to one and forgotten in the other fails here, not only against a
real deployed stack the next time someone happens to run a live
burst/race test. See docs/DECISIONS.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Template
from stacks.data_stack import DataStack
from stacks.orchestration_stack import OrchestrationStack

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import stress_test  # noqa: E402

from tests.test_orchestration_stack import _asl_definition  # noqa: E402


def _synth_orchestration_stack() -> Template:
    app = cdk.App()
    data_stack = DataStack(app, "TestDataStackContract")
    orch_stack = OrchestrationStack(
        app,
        "TestOrchStackContract",
        runs_table=data_stack.runs_table,
        evidence_bucket=data_stack.evidence_bucket,
        lambda_asset_dir=Path(__file__).resolve().parent.parent / "lambda_src",
    )
    return Template.from_stack(orch_stack)


def _invoke_agent_required_payload_fields() -> set[str]:
    definition = _asl_definition(_synth_orchestration_stack())
    payload = definition["States"]["InvokeAgent"]["Parameters"]["Payload"]
    # Each real field is a key ending in ".$" (a JSONPath reference) --
    # strip that suffix to get the plain execution-input field name.
    return {key[:-2] for key in payload if key.endswith(".$")}


def test_burst_async_execution_input_satisfies_the_invoke_agent_payload_contract():
    required = _invoke_agent_required_payload_fields()
    built = stress_test._full_execution_input("r1", "a question")
    missing = required - built.keys()
    assert not missing, f"stress_test.py's execution input is missing field(s) InvokeAgent's Payload requires: {missing}"


def test_race_uses_the_same_shared_input_builder_as_burst_async():
    """Regression guard for the actual root-cause fix: `cmd_race` used to
    build its own, separately-duplicated input dict -- a new required
    field could be added to one call site and forgotten in the other
    (exactly what happened for `engine`/`prior_run_ids`). Reading the
    function's own source confirms `cmd_race` now calls
    `_full_execution_input` instead of constructing its own dict inline."""
    import inspect

    source = inspect.getsource(stress_test.cmd_race)
    assert "_full_execution_input(" in source
