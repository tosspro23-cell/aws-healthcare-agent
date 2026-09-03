"""ApiStack: API Gateway HTTP API + Lambda integration.

Phase 1 scope only: a single anonymous `POST /ask` route. Cognito auth is
deliberately deferred to Phase 2 (see `../../docs/AWS_ROADMAP.md`) so this
skeleton isn't blocked on auth configuration before it can be exercised
end to end.
"""

import sys
from pathlib import Path

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_integrations
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_s3 as s3
from constructs import Construct

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from build_lambda_asset import build_lambda_asset  # noqa: E402


class ApiStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        runs_table: dynamodb.Table,
        evidence_bucket: s3.Bucket,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        lambda_asset_dir = build_lambda_asset()

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
            },
        )
        runs_table.grant_read_write_data(ask_handler)
        evidence_bucket.grant_read_write(ask_handler)

        http_api = apigwv2.HttpApi(self, "CareAgentApi", api_name="care-agent-api")
        ask_integration = apigwv2_integrations.HttpLambdaIntegration("AskIntegration", ask_handler)
        http_api.add_routes(
            path="/ask",
            methods=[apigwv2.HttpMethod.POST],
            integration=ask_integration,
        )

        self.api_url = http_api.api_endpoint
        CfnOutput(self, "ApiUrl", value=http_api.api_endpoint or "")
