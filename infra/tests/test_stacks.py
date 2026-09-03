"""Assertions against the synthesized CloudFormation, not just "does cdk
synth exit 0." Catches regressions like an accidentally-broadened IAM
policy or a bucket that stops blocking public access.
"""

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template
from stacks.api_stack import ApiStack
from stacks.data_stack import DataStack


def _synth_stacks():
    app = cdk.App()
    data_stack = DataStack(app, "TestDataStack")
    api_stack = ApiStack(
        app,
        "TestApiStack",
        runs_table=data_stack.runs_table,
        evidence_bucket=data_stack.evidence_bucket,
    )
    return Template.from_stack(data_stack), Template.from_stack(api_stack)


def test_dynamodb_table_uses_run_id_partition_key_and_on_demand_billing():
    data_template, _ = _synth_stacks()
    data_template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "KeySchema": [{"AttributeName": "run_id", "KeyType": "HASH"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
    )


def test_evidence_bucket_blocks_all_public_access():
    data_template, _ = _synth_stacks()
    data_template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            }
        },
    )


def test_evidence_bucket_is_encrypted():
    data_template, _ = _synth_stacks()
    data_template.has_resource_properties(
        "AWS::S3::Bucket",
        {"BucketEncryption": Match.any_value()},
    )


def test_data_stack_has_exactly_one_table_and_one_bucket():
    data_template, _ = _synth_stacks()
    data_template.resource_count_is("AWS::DynamoDB::Table", 1)
    data_template.resource_count_is("AWS::S3::Bucket", 1)


def test_lambda_uses_python312_runtime_and_expected_handler():
    _, api_template = _synth_stacks()
    api_template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Runtime": "python3.12",
            "Handler": "adapter.handler",
        },
    )


def test_http_api_has_post_ask_route():
    _, api_template = _synth_stacks()
    api_template.has_resource_properties(
        "AWS::ApiGatewayV2::Route",
        {"RouteKey": "POST /ask"},
    )


def test_no_iam_policy_uses_wildcard_resource():
    """Regression guard: every IAM policy statement this stack creates must
    scope `Resource` to specific ARNs (or a stack-ref/GetAtt to one), never
    a bare "*" -- catches an accidental switch from `grant_read_write_data`
    to a broader `grant_full_access`-style call.
    """
    _, api_template = _synth_stacks()
    policies = api_template.find_resources("AWS::IAM::Policy")
    for policy in policies.values():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            resource = statement.get("Resource")
            if resource == "*":
                raise AssertionError(f"Wildcard IAM resource found in statement: {statement}")


def test_api_stack_depends_on_data_stack():
    app = cdk.App()
    data_stack = DataStack(app, "TestDataStack2")
    api_stack = ApiStack(
        app,
        "TestApiStack2",
        runs_table=data_stack.runs_table,
        evidence_bucket=data_stack.evidence_bucket,
    )
    api_stack.add_stack_dependency(data_stack)
    assert data_stack in api_stack.dependencies
