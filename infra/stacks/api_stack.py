"""ApiStack: API Gateway HTTP API + Lambda integrations.

Phase 1 shipped a single anonymous `POST /ask` route (synchronous).
Phase 2 added a Cognito JWT authorizer on it -- auth is enforced entirely
at the API Gateway layer (a request without a valid token never reaches a
Lambda), so no handler needed any auth-specific code for that.
Phase 3 adds the async equivalent: `POST /runs` (start), `GET /runs/{run_id}`
(poll), `POST /runs/{run_id}/cancel` -- all protected by the same
authorizer, all backed by Lambdas owned by `OrchestrationStack`. The
stress-test follow-up adds `POST /jobs` (the SQS-buffered alternative to
`/runs`, backed by `QueueStack`) -- it reuses the same `GET /runs/{run_id}`
route to poll, since `get_run.py` just returns whatever's under `run_id`
regardless of which path wrote it.
"""

from pathlib import Path

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_authorizers as apigwv2_authorizers
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_integrations
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_s3 as s3
from constructs import Construct

from stacks.bedrock_grant import grant_bedrock_invoke

# Every route here is either free/cheap (API Gateway, Lambda, DynamoDB) or
# billed per-token (Bedrock, via /ask, /runs, and /jobs). This app has no
# per-user rate limiting of its own -- a leaked credential or a client bug
# stuck in a retry loop has no in-app ceiling on how fast it can run up
# Bedrock cost. A stage-wide throttle is a coarse, blunt backstop for
# exactly that: well above anything a human clicking through the Workbench
# would ever hit, far below what would let a runaway loop matter much
# before someone notices.
_STAGE_THROTTLE_RATE_LIMIT = 5.0  # sustained requests/second, across all routes
_STAGE_THROTTLE_BURST_LIMIT = 10


class ApiStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        lambda_asset_dir: Path,
        runs_table: dynamodb.Table,
        evidence_bucket: s3.Bucket,
        user_pool: cognito.UserPool,
        app_client: cognito.UserPoolClient,
        start_run_handler: _lambda.Function,
        get_run_handler: _lambda.Function,
        cancel_run_handler: _lambda.Function,
        enqueue_job_handler: _lambda.Function,
        extra_cors_origins: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        ask_handler = _lambda.Function(
            self,
            "AskHandler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="adapter.handler",
            code=_lambda.Code.from_asset(str(lambda_asset_dir)),
            timeout=Duration.seconds(30),
            memory_size=512,
            environment={
                "RUNS_TABLE_NAME": runs_table.table_name,
                "EVIDENCE_BUCKET_NAME": evidence_bucket.bucket_name,
                "CARE_AGENT_NARRATOR_BACKEND": "bedrock",
            },
        )
        # adapter.py only ever put_item/update_item (never reads a run
        # record back, never deletes, never batch-writes) and put_object
        # (never reads evidence back) -- an independent review found the
        # original grant_read_write_data/grant_read_write exceeded what the
        # handler does, and a second review found that even
        # grant_write_data still over-grants: it includes DeleteItem and
        # BatchWriteItem, neither of which this handler calls. Enumerating
        # exactly the two actions used keeps the grant tied to actual
        # behavior instead of a broader convenience bucket.
        runs_table.grant(ask_handler, "dynamodb:PutItem", "dynamodb:UpdateItem")
        evidence_bucket.grant_put(ask_handler)
        grant_bedrock_invoke(ask_handler)

        http_api = apigwv2.HttpApi(
            self,
            "CareAgentApi",
            api_name="care-agent-api",
            # Phase 6's Workbench is the first *browser* caller this API has
            # ever had -- every prior caller (curl, pytest, boto3, the
            # stress-test harness) is same-origin-exempt by construction, so
            # CORS was never needed until now. Always includes the local
            # dev origin (matching the one redirect URI Cognito's App
            # Client always allows -- see `auth_stack.py`); `extra_cors_origins`
            # adds the real hosted Workbench URL once `FrontendStack` exists,
            # never a wildcard "*".
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=["http://localhost:8765", *(extra_cors_origins or [])],
                allow_methods=[apigwv2.CorsHttpMethod.GET, apigwv2.CorsHttpMethod.POST],
                allow_headers=["authorization", "content-type"],
            ),
        )

        # HttpApi's own `HttpStageProps` has a `throttle` field, but
        # HttpApi's constructor (used above) has no way to pass it through
        # to the default stage it auto-creates -- and re-creating that
        # stage explicitly (`create_default_stage=False` + a new
        # `HttpStage` construct) is a logical-ID change CloudFormation
        # rejects outright once the original auto-created stage is already
        # deployed ("Resource ... already exists", confirmed live against
        # this exact stack). An L1 property override on the existing
        # auto-created stage updates it in place instead.
        assert http_api.default_stage is not None
        cfn_stage = http_api.default_stage.node.default_child
        assert isinstance(cfn_stage, apigwv2.CfnStage)
        cfn_stage.add_property_override("DefaultRouteSettings.ThrottlingRateLimit", _STAGE_THROTTLE_RATE_LIMIT)
        cfn_stage.add_property_override("DefaultRouteSettings.ThrottlingBurstLimit", _STAGE_THROTTLE_BURST_LIMIT)

        authorizer = apigwv2_authorizers.HttpJwtAuthorizer(
            "CognitoAuthorizer",
            jwt_issuer=f"https://cognito-idp.{self.region}.amazonaws.com/{user_pool.user_pool_id}",
            jwt_audience=[app_client.user_pool_client_id],
        )

        http_api.add_routes(
            path="/ask",
            methods=[apigwv2.HttpMethod.POST],
            integration=apigwv2_integrations.HttpLambdaIntegration("AskIntegration", ask_handler),
            authorizer=authorizer,
        )
        http_api.add_routes(
            path="/runs",
            methods=[apigwv2.HttpMethod.POST],
            integration=apigwv2_integrations.HttpLambdaIntegration("StartRunIntegration", start_run_handler),
            authorizer=authorizer,
        )
        http_api.add_routes(
            path="/runs/{run_id}",
            methods=[apigwv2.HttpMethod.GET],
            integration=apigwv2_integrations.HttpLambdaIntegration("GetRunIntegration", get_run_handler),
            authorizer=authorizer,
        )
        http_api.add_routes(
            path="/runs/{run_id}/cancel",
            methods=[apigwv2.HttpMethod.POST],
            integration=apigwv2_integrations.HttpLambdaIntegration("CancelRunIntegration", cancel_run_handler),
            authorizer=authorizer,
        )
        http_api.add_routes(
            path="/jobs",
            methods=[apigwv2.HttpMethod.POST],
            integration=apigwv2_integrations.HttpLambdaIntegration("EnqueueJobIntegration", enqueue_job_handler),
            authorizer=authorizer,
        )

        self.api_url = http_api.api_endpoint
        CfnOutput(self, "ApiUrl", value=http_api.api_endpoint or "")
