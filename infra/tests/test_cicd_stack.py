"""Assertions against CiCdStack's synthesized CloudFormation -- the
GitHub Actions OIDC trust that lets CI deploy this app without a stored
AWS credential (see `../stacks/cicd_stack.py` for the full reasoning).

The point of these tests isn't "does an OIDC provider exist" (trivially
true) -- it's that the trust condition is scoped as narrowly as the
stack's own docstring claims: exact repo, exact ref, `StringEquals` (not
`StringLike`, which could be tricked with a crafted ref), and the
assumable role list is exactly the four CDK bootstrap roles, never a
wildcard resource.
"""

import json

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template
from stacks.cicd_stack import CiCdStack

from tests.iam_assertions import assert_no_overly_broad_iam_policy


def _synth_cicd_stack(**kwargs):
    app = cdk.App()
    stack = CiCdStack(app, "TestCiCdStack", env=cdk.Environment(account="123456789012", region="us-east-1"), **kwargs)
    return Template.from_stack(stack)


def test_exactly_one_oidc_provider_for_github_actions():
    template = _synth_cicd_stack()
    providers = template.find_resources("Custom::AWSCDKOpenIdConnectProvider")
    assert len(providers) == 1
    (provider,) = providers.values()
    assert provider["Properties"]["Url"] == "https://token.actions.githubusercontent.com"
    assert provider["Properties"]["ClientIDList"] == ["sts.amazonaws.com"]


def test_trust_policy_is_scoped_to_the_exact_repo_and_ref_with_string_equals():
    """Regression guard: a `StringLike` condition (or a wildcard in the
    `sub` value) would let a crafted ref or a similarly-named fork
    potentially match -- this must be an exact match."""
    template = _synth_cicd_stack(github_repo="someone/example-repo", deploy_ref="refs/heads/main")
    roles = template.find_resources(
        "AWS::IAM::Role", {"Properties": {"Description": Match.string_like_regexp("Assumed by GitHub Actions")}}
    )
    (role,) = roles.values()
    statement = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
    condition = statement["Condition"]
    assert "StringEquals" in condition
    assert "StringLike" not in condition
    assert condition["StringEquals"]["token.actions.githubusercontent.com:sub"] == "repo:someone/example-repo:ref:refs/heads/main"
    assert condition["StringEquals"]["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"


def test_deploy_role_can_only_assume_the_four_cdk_bootstrap_roles():
    template = _synth_cicd_stack()
    policies = template.find_resources("AWS::IAM::Policy")
    assume_role_statements = [
        stmt
        for policy in policies.values()
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]
        if stmt["Action"] == "sts:AssumeRole"
    ]
    assert len(assume_role_statements) == 1
    resources = assume_role_statements[0]["Resource"]
    assert isinstance(resources, list)
    assert len(resources) == 4
    for resource in resources:
        resource_str = json.dumps(resource)
        assert "cdk-hnb659fds-" in resource_str
        assert "role-" in resource_str


def test_no_iam_policy_uses_wildcard_resource():
    assert_no_overly_broad_iam_policy(_synth_cicd_stack())


def test_stack_outputs_the_deploy_role_arn():
    template = _synth_cicd_stack()
    template.has_output("GitHubActionsDeployRoleArn", {})
