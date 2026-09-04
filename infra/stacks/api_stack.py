"""ApiStack: API Gateway HTTP API + Lambda integrations.

Phase 1 shipped a single anonymous `POST /ask` route (synchronous).
Phase 2 added a Cognito JWT authorizer on it -- auth is enforced entirely
at the API Gateway layer (a request without a valid token never reaches a
Lambda), so no handler needed any auth-specific code for that.
Phase 3 adds the async equivalent: `POST /runs` (start), `GET /runs/{run_id}`
(poll), `POST /runs/{run_id}/cancel` -- all protected by the same
authorizer, all backed by Lambdas owned by `OrchestrationStack`.
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
        runs_table.grant_read_write_data(ask_handler)
        evidence_bucket.grant_read_write(ask_handler)
        grant_bedrock_invoke(ask_handler)

        http_api = apigwv2.HttpApi(self, "CareAgentApi", api_name="care-agent-api")

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

        self.api_url = http_api.api_endpoint
        CfnOutput(self, "ApiUrl", value=http_api.api_endpoint or "")
