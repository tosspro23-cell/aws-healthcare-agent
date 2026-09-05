#!/usr/bin/env python3
"""CDK entrypoint. Wires AuthStack (identity), DataStack (state),
OrchestrationStack (async Step Functions run path), and QueueStack
(async SQS-buffered run path) into ApiStack (compute + routing).

Environment is picked up from the CLI's current AWS profile/region
(`CDK_DEFAULT_ACCOUNT` / `CDK_DEFAULT_REGION`, set automatically by the CDK
CLI from your configured credentials) rather than hardcoded, so this app
deploys to whichever account/region `aws configure` currently points at.

The stack-building logic lives in `build_app()` rather than directly at
module level, so `tests/test_app.py` can import and call it to get real
stack instances wired exactly as this file wires them -- including their
`add_stack_dependency` calls -- rather than a test-only reconstruction
that could silently drift from what this file actually does. An
independent review found the existing dependency test only proved CDK's
own `add_stack_dependency`/`.dependencies` work, since the test called
`add_stack_dependency` itself rather than exercising this file's code;
see docs/DECISIONS.md.
"""

import os
from dataclasses import dataclass

import aws_cdk as cdk
from build_lambda_asset import build_lambda_asset
from stacks.api_stack import ApiStack
from stacks.auth_stack import AuthStack
from stacks.data_stack import DataStack
from stacks.orchestration_stack import OrchestrationStack
from stacks.queue_stack import QueueStack


@dataclass
class AppStacks:
    app: cdk.App
    auth_stack: AuthStack
    data_stack: DataStack
    orchestration_stack: OrchestrationStack
    queue_stack: QueueStack
    api_stack: ApiStack


def build_app() -> AppStacks:
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

    queue_stack = QueueStack(
        app,
        "CareAgentQueueStack",
        runs_table=data_stack.runs_table,
        lambda_asset_dir=lambda_asset_dir,
        env=env,
    )
    queue_stack.add_stack_dependency(data_stack)

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
        enqueue_job_handler=queue_stack.enqueue_job_handler,
        env=env,
    )
    api_stack.add_stack_dependency(data_stack)
    api_stack.add_stack_dependency(auth_stack)
    api_stack.add_stack_dependency(orchestration_stack)
    api_stack.add_stack_dependency(queue_stack)

    return AppStacks(
        app=app,
        auth_stack=auth_stack,
        data_stack=data_stack,
        orchestration_stack=orchestration_stack,
        queue_stack=queue_stack,
        api_stack=api_stack,
    )


if __name__ == "__main__":
    build_app().app.synth()
