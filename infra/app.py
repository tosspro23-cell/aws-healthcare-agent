#!/usr/bin/env python3
"""CDK entrypoint. Wires AuthStack (identity) and DataStack (state) into
ApiStack (compute + routing).

Environment is picked up from the CLI's current AWS profile/region
(`CDK_DEFAULT_ACCOUNT` / `CDK_DEFAULT_REGION`, set automatically by the CDK
CLI from your configured credentials) rather than hardcoded, so this app
deploys to whichever account/region `aws configure` currently points at.
"""

import os

import aws_cdk as cdk
from stacks.api_stack import ApiStack
from stacks.auth_stack import AuthStack
from stacks.data_stack import DataStack

app = cdk.App()

env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=os.getenv("CDK_DEFAULT_REGION"),
)

auth_stack = AuthStack(app, "CareAgentAuthStack", env=env)
data_stack = DataStack(app, "CareAgentDataStack", env=env)

api_stack = ApiStack(
    app,
    "CareAgentApiStack",
    runs_table=data_stack.runs_table,
    evidence_bucket=data_stack.evidence_bucket,
    user_pool=auth_stack.user_pool,
    app_client=auth_stack.app_client,
    env=env,
)
api_stack.add_stack_dependency(data_stack)
api_stack.add_stack_dependency(auth_stack)

app.synth()
