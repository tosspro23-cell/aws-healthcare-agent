#!/usr/bin/env python3
"""CDK entrypoint. Wires AuthStack (identity), DataStack (state), and
OrchestrationStack (async Step Functions run path) into ApiStack
(compute + routing).

Environment is picked up from the CLI's current AWS profile/region
(`CDK_DEFAULT_ACCOUNT` / `CDK_DEFAULT_REGION`, set automatically by the CDK
CLI from your configured credentials) rather than hardcoded, so this app
deploys to whichever account/region `aws configure` currently points at.
"""

import os

import aws_cdk as cdk
from build_lambda_asset import build_lambda_asset
from stacks.api_stack import ApiStack
from stacks.auth_stack import AuthStack
from stacks.data_stack import DataStack
from stacks.orchestration_stack import OrchestrationStack

app = cdk.App()

env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=os.getenv("CDK_DEFAULT_REGION"),
)

# Built once, shared by every Lambda Function across every stack (each
# points its own `handler` at a different module within this same asset).
lambda_asset_dir = build_lambda_asset()

auth_stack = AuthStack(app, "CareAgentAuthStack", env=env)
data_stack = DataStack(app, "CareAgentDataStack", env=env)

orchestration_stack = OrchestrationStack(
    app,
    "CareAgentOrchestrationStack",
    runs_table=data_stack.runs_table,
    lambda_asset_dir=lambda_asset_dir,
    env=env,
)
orchestration_stack.add_stack_dependency(data_stack)

api_stack = ApiStack(
    app,
    "CareAgentApiStack",
    lambda_asset_dir=lambda_asset_dir,
    runs_table=data_stack.runs_table,
    evidence_bucket=data_stack.evidence_bucket,
    user_pool=auth_stack.user_pool,
    app_client=auth_stack.app_client,
    start_run_handler=orchestration_stack.start_run_handler,
    get_run_handler=orchestration_stack.get_run_handler,
    cancel_run_handler=orchestration_stack.cancel_run_handler,
    env=env,
)
api_stack.add_stack_dependency(data_stack)
api_stack.add_stack_dependency(auth_stack)
api_stack.add_stack_dependency(orchestration_stack)

app.synth()
