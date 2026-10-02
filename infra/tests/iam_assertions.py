"""Shared IAM-policy assertion used across test_stacks.py,
test_orchestration_stack.py, and test_queue_stack.py.

Strengthened after an independent review found the original version (used
independently, near-identically, in all three files) only rejected a bare
`Resource: "*"` -- missing a wildcard hidden inside a `Resource` *list*
(`["*", "arn:...:something"]"` would pass, since the whole list never
equals the string `"*"`), and never checked for an action-level wildcard
(`"Action": "s3:*"`) at all. See docs/DECISIONS.md and
docs/INDEPENDENT_REVIEW_FINDINGS.md (finding #11).

Deliberately does *not* flag a resource wildcard that's scoped to a
specific ARN prefix (e.g. `arn:aws:s3:::some-bucket/*`, which CDK's
`grant_read`/`grant_write` generate for "every object in this specific
bucket") -- that's a legitimate, narrow suffix wildcard, not an
unrestricted one. Only a wildcard that stands alone as an entire path
segment (the bare string `"*"`, or `"*"` as one full element of a
`Resource` list) is flagged -- *unless* every action on that exact
statement is one of the handful of AWS APIs that genuinely don't support
resource-level scoping at all (see `_RESOURCE_WILDCARD_REQUIRED_ACTIONS`
below): CDK's own generated policy for a Step Functions `StateMachine`
with `logs=`/`tracing_enabled=True` bundles these under `Resource: "*"`
with no way to narrow it further, confirmed directly against AWS's own
IAM action reference, not assumed. A statement mixing in any action
outside that exact set still fails -- this can't accidentally shadow an
unrelated overly-broad grant added later.
"""

from __future__ import annotations

from aws_cdk.assertions import Template

_RESOURCE_WILDCARD_REQUIRED_ACTIONS = {
    "logs:CreateLogDelivery",
    "logs:DeleteLogDelivery",
    "logs:DescribeLogGroups",
    "logs:DescribeResourcePolicies",
    "logs:GetLogDelivery",
    "logs:ListLogDeliveries",
    "logs:PutResourcePolicy",
    "logs:UpdateLogDelivery",
    "xray:GetSamplingRules",
    "xray:GetSamplingTargets",
    "xray:PutTelemetryRecords",
    "xray:PutTraceSegments",
}


def assert_handler_has_iam_actions(template: Template, handler_logical_id_fragment: str, expected_actions: list[str]) -> None:
    """Asserts the *specific* role attached to the Lambda function whose
    logical id contains `handler_logical_id_fragment` (e.g. "AgentTaskHandler")
    has every action in `expected_actions` somewhere in its own policy --
    not "this action exists somewhere in the stack," which would pass even
    if it were accidentally granted to a different handler entirely.

    Mirrors the pattern independently proven in
    `test_orchestration_stack.py::test_start_run_handler_can_describe_executions`
    -- factored out here once it was needed a third time (AgentTaskHandler,
    ProcessJobHandler, AskHandler all needed the identical shape of check
    for the same real regression: a missing `dynamodb:GetItem`/
    `s3:GetObject` grant that crashed each handler the first time it
    actually tried to read a prior run's evidence). See docs/DECISIONS.md.
    """
    functions = template.find_resources("AWS::Lambda::Function")
    (logical_id,) = (lid for lid in functions if handler_logical_id_fragment in lid)
    role_ref = functions[logical_id]["Properties"]["Role"]["Fn::GetAtt"][0]

    policies = template.find_resources("AWS::IAM::Policy")
    matching_actions: list[str] = []
    for policy in policies.values():
        roles = policy["Properties"].get("Roles", [])
        if not any(isinstance(r, dict) and r.get("Ref") == role_ref for r in roles):
            continue
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            action = statement.get("Action")
            matching_actions.extend(action if isinstance(action, list) else [action])

    for expected in expected_actions:
        assert expected in matching_actions, f"{handler_logical_id_fragment} is missing {expected!r}: has {matching_actions}"


def assert_no_overly_broad_iam_policy(template: Template) -> None:
    policies = template.find_resources("AWS::IAM::Policy")
    for logical_id, policy in policies.items():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            resource = statement.get("Resource")
            resources = resource if isinstance(resource, list) else [resource]
            action = statement.get("Action")
            actions = action if isinstance(action, list) else [action]

            if any(entry == "*" for entry in resources):
                if actions and all(isinstance(a, str) and a in _RESOURCE_WILDCARD_REQUIRED_ACTIONS for a in actions):
                    continue
                raise AssertionError(f"Wildcard IAM resource in {logical_id}: {statement}")

            for entry in actions:
                if isinstance(entry, str) and (entry == "*" or entry.endswith(":*")):
                    raise AssertionError(f"Wildcard IAM action ({entry!r}) in {logical_id}: {statement}")
