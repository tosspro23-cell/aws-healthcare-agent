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
`Resource` list) is flagged.
"""

from __future__ import annotations

from aws_cdk.assertions import Template


def assert_no_overly_broad_iam_policy(template: Template) -> None:
    policies = template.find_resources("AWS::IAM::Policy")
    for logical_id, policy in policies.items():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            resource = statement.get("Resource")
            resources = resource if isinstance(resource, list) else [resource]
            for entry in resources:
                if entry == "*":
                    raise AssertionError(f"Wildcard IAM resource in {logical_id}: {statement}")

            action = statement.get("Action")
            actions = action if isinstance(action, list) else [action]
            for entry in actions:
                if isinstance(entry, str) and (entry == "*" or entry.endswith(":*")):
                    raise AssertionError(f"Wildcard IAM action ({entry!r}) in {logical_id}: {statement}")
