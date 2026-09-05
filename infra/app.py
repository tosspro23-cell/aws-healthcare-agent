#!/usr/bin/env python3
"""CDK entrypoint. Wires AuthStack (identity), DataStack (state),
OrchestrationStack (async Step Functions run path), and QueueStack
(async SQS-buffered run path) into ApiStack (compute + routing), plus
FrontendStack (the hosted Workbench itself).

Environment is picked up from the CLI's current AWS profile/region
(`CDK_DEFAULT_ACCOUNT` / `CDK_DEFAULT_REGION`, set automatically by the CDK
CLI from your configured credentials) rather than hardcoded, so this app
deploys to whichever account/region `aws configure` currently points at.

**Two-pass deployment for the hosted Workbench** (see `frontend_stack.py`'s
own docstring for the full reasoning): `FrontendStack`'s CloudFront domain
isn't known until after it's first deployed, but `AuthStack`'s Cognito App
Client and `ApiStack`'s CORS need that exact domain registered before a
browser served from it can complete a real login or call the API.
`CARE_AGENT_WORKBENCH_URL`, if set, is threaded into both -- leave it unset
for the first deploy, then set it to the URL `cdk deploy` printed
(`CareAgentFrontendStack.WorkbenchUrl`) and deploy again.

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
from build_frontend_asset import build_frontend_asset
from build_lambda_asset import build_lambda_asset
from stacks.api_stack import ApiStack
from stacks.auth_stack import AuthStack
from stacks.data_stack import DataStack
from stacks.frontend_stack import FrontendStack
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
    frontend_stack: FrontendStack


def build_app() -> AppStacks:
    app = cdk.App()

    env = cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION"),
    )

    # Set after FrontendStack's first deploy -- see this module's docstring.
    workbench_url = os.getenv("CARE_AGENT_WORKBENCH_URL")

    # Built once, shared by every Lambda Function across every stack (each
    # points its own `handler` at a different module within this same asset).
    lambda_asset_dir = build_lambda_asset()
    frontend_build_dir = build_frontend_asset()

    auth_stack = AuthStack(
        app,
        "CareAgentAuthStack",
        additional_callback_urls=[f"{workbench_url}/callback"] if workbench_url else None,
        additional_logout_urls=[f"{workbench_url}/logout"] if workbench_url else None,
        env=env,
    )
    data_stack = DataStack(app, "CareAgentDataStack", env=env)

    orchestration_stack = OrchestrationStack(
        app,
        "CareAgentOrchestrationStack",
        runs_table=data_stack.runs_table,
        evidence_bucket=data_stack.evidence_bucket,
        lambda_asset_dir=lambda_asset_dir,
        env=env,
    )
    orchestration_stack.add_stack_dependency(data_stack)

    queue_stack = QueueStack(
        app,
        "CareAgentQueueStack",
        runs_table=data_stack.runs_table,
        evidence_bucket=data_stack.evidence_bucket,
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
        extra_cors_origins=[workbench_url] if workbench_url else None,
        env=env,
    )
    api_stack.add_stack_dependency(data_stack)
    api_stack.add_stack_dependency(auth_stack)
    api_stack.add_stack_dependency(orchestration_stack)
    api_stack.add_stack_dependency(queue_stack)

    frontend_stack = FrontendStack(
        app,
        "CareAgentFrontendStack",
        frontend_build_dir=frontend_build_dir,
        env=env,
    )

    return AppStacks(
        app=app,
        auth_stack=auth_stack,
        data_stack=data_stack,
        orchestration_stack=orchestration_stack,
        queue_stack=queue_stack,
        api_stack=api_stack,
        frontend_stack=frontend_stack,
    )


if __name__ == "__main__":
    build_app().app.synth()
