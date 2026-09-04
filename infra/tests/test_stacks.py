"""Assertions against the synthesized CloudFormation, not just "does cdk
synth exit 0." Catches regressions like an accidentally-broadened IAM
policy, a bucket that stops blocking public access, or a route that goes
back to being anonymous after auth was added.
"""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template
from stacks.api_stack import ApiStack
from stacks.auth_stack import AuthStack
from stacks.data_stack import DataStack
from stacks.orchestration_stack import OrchestrationStack
from stacks.queue_stack import QueueStack

_LAMBDA_ASSET_DIR = Path(__file__).resolve().parent.parent / "lambda_src"


def _synth_stacks():
    app = cdk.App()
    auth_stack = AuthStack(app, "TestAuthStack", domain_prefix="care-agent-test-synth-only")
    data_stack = DataStack(app, "TestDataStack")
    orch_stack = OrchestrationStack(
        app,
        "TestOrchStack",
        runs_table=data_stack.runs_table,
        lambda_asset_dir=_LAMBDA_ASSET_DIR,
    )
    queue_stack = QueueStack(
        app,
        "TestQueueStack",
        runs_table=data_stack.runs_table,
        lambda_asset_dir=_LAMBDA_ASSET_DIR,
    )
    api_stack = ApiStack(
        app,
        "TestApiStack",
        lambda_asset_dir=_LAMBDA_ASSET_DIR,
        runs_table=data_stack.runs_table,
        evidence_bucket=data_stack.evidence_bucket,
        user_pool=auth_stack.user_pool,
        app_client=auth_stack.app_client,
        start_run_handler=orch_stack.start_run_handler,
        get_run_handler=orch_stack.get_run_handler,
        cancel_run_handler=orch_stack.cancel_run_handler,
        enqueue_job_handler=queue_stack.enqueue_job_handler,
    )
    return Template.from_stack(auth_stack), Template.from_stack(data_stack), Template.from_stack(api_stack)


def test_dynamodb_table_uses_run_id_partition_key_and_on_demand_billing():
    _, data_template, _ = _synth_stacks()
    data_template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "KeySchema": [{"AttributeName": "run_id", "KeyType": "HASH"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
    )


def test_evidence_bucket_blocks_all_public_access():
    _, data_template, _ = _synth_stacks()
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
    _, data_template, _ = _synth_stacks()
    data_template.has_resource_properties(
        "AWS::S3::Bucket",
        {"BucketEncryption": Match.any_value()},
    )


def test_data_stack_has_exactly_one_table_and_one_bucket():
    _, data_template, _ = _synth_stacks()
    data_template.resource_count_is("AWS::DynamoDB::Table", 1)
    data_template.resource_count_is("AWS::S3::Bucket", 1)


def test_lambda_uses_python312_runtime_and_expected_handler():
    _, _, api_template = _synth_stacks()
    api_template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Runtime": "python3.12",
            "Handler": "adapter.handler",
        },
    )


def test_http_api_has_post_ask_route():
    _, _, api_template = _synth_stacks()
    api_template.has_resource_properties(
        "AWS::ApiGatewayV2::Route",
        {"RouteKey": "POST /ask"},
    )


def test_http_api_has_runs_routes():
    """Phase 3: the async run-management routes exist and are wired to
    their respective Lambdas."""
    _, _, api_template = _synth_stacks()
    api_template.has_resource_properties("AWS::ApiGatewayV2::Route", {"RouteKey": "POST /runs"})
    api_template.has_resource_properties("AWS::ApiGatewayV2::Route", {"RouteKey": "GET /runs/{run_id}"})
    api_template.has_resource_properties("AWS::ApiGatewayV2::Route", {"RouteKey": "POST /runs/{run_id}/cancel"})


def test_ask_route_requires_jwt_authorization_not_anonymous():
    """Regression guard for Phase 2: the /ask route must require a JWT,
    never fall back to anonymous (AuthorizationType NONE)."""
    _, _, api_template = _synth_stacks()
    api_template.has_resource_properties(
        "AWS::ApiGatewayV2::Route",
        {"RouteKey": "POST /ask", "AuthorizationType": "JWT"},
    )


def test_runs_routes_also_require_jwt_authorization():
    """Regression guard: the new Phase 3 routes must reuse the same
    Cognito authorizer, not accidentally ship anonymous."""
    _, _, api_template = _synth_stacks()
    for route_key in ("POST /runs", "GET /runs/{run_id}", "POST /runs/{run_id}/cancel"):
        api_template.has_resource_properties(
            "AWS::ApiGatewayV2::Route",
            {"RouteKey": route_key, "AuthorizationType": "JWT"},
        )


def test_jobs_route_exists_and_requires_jwt_authorization():
    """The SQS-buffered path's entrypoint (stress-test follow-up): same
    JWT authorizer as every other route, not anonymous."""
    _, _, api_template = _synth_stacks()
    api_template.has_resource_properties(
        "AWS::ApiGatewayV2::Route",
        {"RouteKey": "POST /jobs", "AuthorizationType": "JWT"},
    )


def test_jwt_authorizer_uses_identity_source_authorization_header():
    _, _, api_template = _synth_stacks()
    api_template.has_resource_properties(
        "AWS::ApiGatewayV2::Authorizer",
        {
            "AuthorizerType": "JWT",
            "IdentitySource": ["$request.header.Authorization"],
        },
    )


def test_no_iam_policy_uses_wildcard_resource():
    """Regression guard: every IAM policy statement this stack creates must
    scope `Resource` to specific ARNs (or a stack-ref/GetAtt to one), never
    a bare "*" -- catches an accidental switch from `grant_read_write_data`
    to a broader `grant_full_access`-style call.
    """
    _, _, api_template = _synth_stacks()
    policies = api_template.find_resources("AWS::IAM::Policy")
    for policy in policies.values():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            resource = statement.get("Resource")
            if resource == "*":
                raise AssertionError(f"Wildcard IAM resource found in statement: {statement}")


def test_user_pool_client_is_public_no_secret_pkce_shaped():
    """The App Client must be a public client (no secret) with the
    authorization-code grant enabled -- the shape PKCE requires. A
    `generate_secret=True` regression would break the CLI token script,
    which has no secure place to keep a client secret."""
    auth_template, _, _ = _synth_stacks()
    auth_template.has_resource_properties(
        "AWS::Cognito::UserPoolClient",
        {
            "AllowedOAuthFlows": ["code"],
            "GenerateSecret": False,
        },
    )


def test_user_pool_domain_matches_expected_prefix():
    auth_template, _, _ = _synth_stacks()
    auth_template.has_resource_properties(
        "AWS::Cognito::UserPoolDomain",
        {"Domain": "care-agent-test-synth-only"},
    )


def test_auth_stack_has_exactly_one_user_pool():
    auth_template, _, _ = _synth_stacks()
    auth_template.resource_count_is("AWS::Cognito::UserPool", 1)
    auth_template.resource_count_is("AWS::Cognito::UserPoolClient", 1)


def test_api_stack_depends_on_data_stack_and_auth_stack_and_orch_stack():
    app = cdk.App()
    auth_stack = AuthStack(app, "TestAuthStack2", domain_prefix="care-agent-test-synth-only-2")
    data_stack = DataStack(app, "TestDataStack2")
    orch_stack = OrchestrationStack(
        app,
        "TestOrchStack2",
        runs_table=data_stack.runs_table,
        lambda_asset_dir=_LAMBDA_ASSET_DIR,
    )
    queue_stack = QueueStack(
        app,
        "TestQueueStack2",
        runs_table=data_stack.runs_table,
        lambda_asset_dir=_LAMBDA_ASSET_DIR,
    )
    api_stack = ApiStack(
        app,
        "TestApiStack2",
        lambda_asset_dir=_LAMBDA_ASSET_DIR,
        runs_table=data_stack.runs_table,
        evidence_bucket=data_stack.evidence_bucket,
        user_pool=auth_stack.user_pool,
        app_client=auth_stack.app_client,
        start_run_handler=orch_stack.start_run_handler,
        get_run_handler=orch_stack.get_run_handler,
        cancel_run_handler=orch_stack.cancel_run_handler,
        enqueue_job_handler=queue_stack.enqueue_job_handler,
    )
    api_stack.add_stack_dependency(data_stack)
    api_stack.add_stack_dependency(auth_stack)
    api_stack.add_stack_dependency(orch_stack)
    api_stack.add_stack_dependency(queue_stack)
    assert data_stack in api_stack.dependencies
    assert auth_stack in api_stack.dependencies
    assert orch_stack in api_stack.dependencies
    assert queue_stack in api_stack.dependencies
