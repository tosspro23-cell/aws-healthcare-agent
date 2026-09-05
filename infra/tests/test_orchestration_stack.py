"""CDK-level assertions for the Phase 3 state machine: retry/timeout/catch
are actually configured, not just "does cdk synth exit 0."
"""

import json
from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Template
from stacks.data_stack import DataStack
from stacks.orchestration_stack import OrchestrationStack

from tests.iam_assertions import assert_no_overly_broad_iam_policy


def _synth_stacks():
    app = cdk.App()
    data_stack = DataStack(app, "TestDataStack3")
    orch_stack = OrchestrationStack(
        app,
        "TestOrchStack",
        runs_table=data_stack.runs_table,
        lambda_asset_dir=Path(__file__).resolve().parent.parent / "lambda_src",
    )
    return Template.from_stack(orch_stack)


def _asl_definition(template: Template) -> dict:
    state_machines = template.find_resources("AWS::StepFunctions::StateMachine")
    assert len(state_machines) == 1
    (resource,) = state_machines.values()
    parts = resource["Properties"]["DefinitionString"]["Fn::Join"][1]
    # Non-string parts (Ref/Fn::GetAtt tokens) sit *inside* an existing JSON
    # string literal (e.g. the "Resource" ARN) -- substitute a bare,
    # unquoted placeholder rather than wrapping it in its own quotes, or
    # the result isn't valid JSON.
    plain = "".join(p if isinstance(p, str) else "PSEUDOREF" for p in parts)
    return json.loads(plain)


def test_state_machine_starts_at_mark_running():
    definition = _asl_definition(_synth_stacks())
    assert definition["StartAt"] == "MarkRunning"


def test_mark_running_task_forwards_owner_sub_in_its_payload():
    """Regression test: mark_running.py requires `event["owner_sub"]`, but
    the Step Functions task's payload mapping (TaskInput.from_object) only
    forwards the specific keys listed there -- it does NOT pass through
    the raw execution input automatically. owner_sub was added to
    start_run.py's execution input and to mark_running.py's own code, but
    initially forgotten here, in the one place that actually controls what
    reaches the Lambda. A moto-mocked unit test calling
    mark_running.handler directly (bypassing this payload-mapping layer
    entirely) couldn't catch this -- only a real Step Functions execution
    did, immediately failing every run with `KeyError: 'owner_sub'`. See
    docs/DECISIONS.md."""
    definition = _asl_definition(_synth_stacks())
    mark_running_params = definition["States"]["MarkRunning"]["Parameters"]
    assert "owner_sub.$" in mark_running_params["Payload"]


def test_invoke_agent_has_a_bounded_timeout():
    definition = _asl_definition(_synth_stacks())
    invoke_agent = definition["States"]["InvokeAgent"]
    assert invoke_agent["TimeoutSeconds"] == 25


def test_narrator_backend_flows_through_invoke_agent_and_into_record_success():
    """Regression test: an independent review found that InvokeAgent's
    ResultSelector only extracted `answer`/`safe` from agent_task.py's
    response, silently dropping `trace.narrator_backend` -- so
    record_result.py could never persist it even after being taught how
    to (see record_result.py's own fix), because it was never in the data
    reaching that state at all. Both the ResultSelector and
    RecordSuccess's own payload mapping need to carry it through."""
    definition = _asl_definition(_synth_stacks())
    invoke_agent = definition["States"]["InvokeAgent"]
    assert "narrator_backend.$" in invoke_agent["ResultSelector"]

    record_success_params = definition["States"]["RecordSuccess"]["Parameters"]
    assert "narrator_backend.$" in record_success_params["Payload"]


def test_invoke_agent_has_our_custom_retry_configured():
    definition = _asl_definition(_synth_stacks())
    invoke_agent = definition["States"]["InvokeAgent"]
    retries = invoke_agent["Retry"]
    custom_retry = next(r for r in retries if "Lambda.TooManyRequestsException" in r["ErrorEquals"])
    assert custom_retry["MaxAttempts"] == 3
    assert custom_retry["BackoffRate"] == 2


def test_our_custom_retry_actually_governs_throttling_not_just_present():
    """The previous test only checked the custom 3-attempt policy exists
    -- it doesn't prove that policy is actually what Step Functions uses
    for a real Lambda.TooManyRequestsException, since CDK also inserts its
    own default retry policy (6 attempts, for a different but overlapping
    set of Lambda error codes) ahead of the custom one in the array (see
    orchestration_stack.py's module docstring and docs/DECISIONS.md). Step
    Functions resolves an error against the *first* Retry entry whose
    ErrorEquals list contains it -- so this asserts the actual, provable
    thing: Lambda.TooManyRequestsException appears in exactly one of the
    two policies, meaning there's no ambiguity about which one applies to
    the specific error this project has actually observed in practice."""
    definition = _asl_definition(_synth_stacks())
    invoke_agent = definition["States"]["InvokeAgent"]
    matching_policies = [r for r in invoke_agent["Retry"] if "Lambda.TooManyRequestsException" in r["ErrorEquals"]]
    assert len(matching_policies) == 1
    assert matching_policies[0]["MaxAttempts"] == 3


def test_invoke_agent_catches_everything_to_is_timeout_choice():
    definition = _asl_definition(_synth_stacks())
    invoke_agent = definition["States"]["InvokeAgent"]
    (catcher,) = invoke_agent["Catch"]
    assert catcher["ErrorEquals"] == ["States.ALL"]
    assert catcher["Next"] == "IsTimeout"
    assert catcher["ResultPath"] == "$.error_info"


def test_is_timeout_choice_routes_timeouts_and_failures_separately():
    definition = _asl_definition(_synth_stacks())
    choice = definition["States"]["IsTimeout"]
    assert choice["Type"] == "Choice"
    (rule,) = choice["Choices"]
    assert rule["Variable"] == "$.error_info.Error"
    assert rule["StringEquals"] == "States.Timeout"
    assert rule["Next"] == "RecordTimeout"
    assert choice["Default"] == "RecordFailure"


def test_success_path_reaches_record_success_and_ends():
    definition = _asl_definition(_synth_stacks())
    assert definition["States"]["InvokeAgent"]["Next"] == "RecordSuccess"
    assert definition["States"]["RecordSuccess"]["End"] is True


def test_failure_and_timeout_paths_both_end():
    definition = _asl_definition(_synth_stacks())
    assert definition["States"]["RecordFailure"]["End"] is True
    assert definition["States"]["RecordTimeout"]["End"] is True


def test_overall_execution_has_a_bounded_timeout():
    definition = _asl_definition(_synth_stacks())
    assert definition["TimeoutSeconds"] == 300


def test_lambda_functions_created_for_every_handler():
    template = _synth_stacks()
    # mark_running, agent_task, record_result, start_run, get_run, cancel_run
    template.resource_count_is("AWS::Lambda::Function", 6)


def test_start_run_handler_can_start_executions():
    """Regression guard: start_run's IAM policy must include
    states:StartExecution -- without it, every POST /runs would 403
    against AWS even though our own code has no bug."""
    template = _synth_stacks()
    policies = template.find_resources("AWS::IAM::Policy")
    actions = []
    for policy in policies.values():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            action = statement.get("Action")
            if isinstance(action, list):
                actions.extend(action)
            elif action:
                actions.append(action)
    assert "states:StartExecution" in actions


def test_cancel_run_handler_can_stop_executions():
    template = _synth_stacks()
    policies = template.find_resources("AWS::IAM::Policy")
    actions = []
    for policy in policies.values():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            action = statement.get("Action")
            if isinstance(action, list):
                actions.extend(action)
            elif action:
                actions.append(action)
    assert "states:StopExecution" in actions


def test_no_iam_policy_uses_wildcard_resource():
    assert_no_overly_broad_iam_policy(_synth_stacks())
