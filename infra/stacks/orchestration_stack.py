"""OrchestrationStack: the Step Functions state machine + supporting
Lambdas for the async run path (Phase 3).

Reliability semantics this implements, matching the challenge/comparison
goal directly (see `../../docs/AWS_ROADMAP.md` Phase 3 and
`../../docs/DECISIONS.md`):

- **start**: `MarkRunning` writes the initial `RUNNING` record.
- **bounded retry**: every `LambdaInvoke` task in this state machine gets
  the same custom `add_retry` (3 attempts, 2s/2x backoff) for
  `Lambda.TooManyRequestsException` specifically -- native Step Functions
  retry, not a hand-rolled loop. Originally only wired onto `InvokeAgent`;
  a live burst test (see `../../docs/STRESS_TEST.md`) found this
  account's Lambda concurrency ceiling could throttle `MarkRunning` too,
  and with no retry there the whole execution failed in under 200ms
  without ever reaching `InvokeAgent`'s retry/catch logic. Note: CDK also
  inserts its *own* default retry policy (6 attempts, for
  `Lambda.ClientExecutionTimeoutException`/`ServiceException`/
  `AWSLambdaException`/`SdkClientException`) onto every `LambdaInvoke`
  task ahead of this custom one -- confirmed by inspecting the synthesized
  ASL, not assumed. Step Functions resolves this by using the first
  `Retry` entry whose `ErrorEquals` list contains the specific error that
  occurred, so the custom policy above is what actually governs
  `TooManyRequestsException` (the only error type this project has
  observed in practice), while the other three error codes fall under
  CDK's own 6-attempt default instead. An independent review caught the
  earlier version of this docstring describing "3 attempts" as a uniform,
  complete policy -- see `docs/DECISIONS.md`.
- **timeout**: `InvokeAgent`'s `task_timeout=` -- native per-task timeout.
- **cancellation**: `../lambda_src/cancel_run.py`, called from outside the
  state machine (via `POST /runs/{run_id}/cancel`) at any time while a run
  is in flight.
- **terminal-state ownership**: `record_result.py`'s conditional DynamoDB
  write (`ConditionExpression: status = RUNNING`) is the single source of
  truth for "who finalized this run" -- the state machine's own success/
  failure path and the external cancel handler race for it, and DynamoDB's
  atomic compare-and-swap decides the winner deterministically. Nothing in
  this stack coordinates that race directly; the database does.

The Lambda that actually calls `care_agent` (`agent_task.py`) is the
*only* place `HealthAgent.ask()` gets invoked here -- everything else in
this stack is pure orchestration plumbing.
"""

from pathlib import Path

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from stacks.bedrock_grant import grant_bedrock_invoke


class OrchestrationStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        runs_table: dynamodb.Table,
        lambda_asset_dir: Path,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        common_env = {"RUNS_TABLE_NAME": runs_table.table_name}

        mark_running_handler = _lambda.Function(
            self,
            "MarkRunningHandler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="mark_running.handler",
            code=_lambda.Code.from_asset(str(lambda_asset_dir)),
            timeout=Duration.seconds(10),
            environment=common_env,
        )
        runs_table.grant_write_data(mark_running_handler)

        agent_task_handler = _lambda.Function(
            self,
            "AgentTaskHandler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="agent_task.handler",
            code=_lambda.Code.from_asset(str(lambda_asset_dir)),
            timeout=Duration.seconds(25),
            memory_size=512,
            environment={"CARE_AGENT_NARRATOR_BACKEND": "bedrock"},
        )
        grant_bedrock_invoke(agent_task_handler)

        record_result_handler = _lambda.Function(
            self,
            "RecordResultHandler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="record_result.handler",
            code=_lambda.Code.from_asset(str(lambda_asset_dir)),
            timeout=Duration.seconds(10),
            environment=common_env,
        )
        runs_table.grant_write_data(record_result_handler)

        self.start_run_handler = _lambda.Function(
            self,
            "StartRunHandler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="start_run.handler",
            code=_lambda.Code.from_asset(str(lambda_asset_dir)),
            timeout=Duration.seconds(10),
        )

        self.get_run_handler = _lambda.Function(
            self,
            "GetRunHandler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="get_run.handler",
            code=_lambda.Code.from_asset(str(lambda_asset_dir)),
            timeout=Duration.seconds(10),
            environment=common_env,
        )
        runs_table.grant_read_data(self.get_run_handler)

        self.cancel_run_handler = _lambda.Function(
            self,
            "CancelRunHandler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="cancel_run.handler",
            code=_lambda.Code.from_asset(str(lambda_asset_dir)),
            timeout=Duration.seconds(10),
            environment=common_env,
        )
        runs_table.grant_read_write_data(self.cancel_run_handler)

        # -- state machine definition ---------------------------------------
        # Bounded retry for transient Lambda-service-level throttling
        # (as opposed to an application error inside the handler, which
        # retrying can't fix). Applied identically to *every* LambdaInvoke
        # task in this state machine, not just InvokeAgent -- a live burst
        # test (see docs/STRESS_TEST.md) found this account's low Lambda
        # concurrency ceiling throttling MarkRunning too, and since it had
        # no retry configured, the whole execution failed within the first
        # ~150ms without ever reaching InvokeAgent's carefully-designed
        # Retry/Catch semantics at all.
        _THROTTLING_ERRORS = [
            "Lambda.ServiceException",
            "Lambda.AWSLambdaException",
            "Lambda.SdkClientException",
            "Lambda.TooManyRequestsException",
        ]

        def _add_throttling_retry(task: tasks.LambdaInvoke) -> None:
            task.add_retry(errors=_THROTTLING_ERRORS, interval=Duration.seconds(2), max_attempts=3, backoff_rate=2.0)

        mark_running_task = tasks.LambdaInvoke(
            self,
            "MarkRunning",
            lambda_function=mark_running_handler,
            payload=sfn.TaskInput.from_object(
                {
                    "run_id": sfn.JsonPath.string_at("$.run_id"),
                    "user_id": sfn.JsonPath.string_at("$.user_id"),
                    "question": sfn.JsonPath.string_at("$.question"),
                    "owner_sub": sfn.JsonPath.string_at("$.owner_sub"),
                }
            ),
            result_path=sfn.JsonPath.DISCARD,
        )
        _add_throttling_retry(mark_running_task)

        invoke_agent_task = tasks.LambdaInvoke(
            self,
            "InvokeAgent",
            lambda_function=agent_task_handler,
            payload=sfn.TaskInput.from_object(
                {
                    "run_id": sfn.JsonPath.string_at("$.run_id"),
                    "user_id": sfn.JsonPath.string_at("$.user_id"),
                    "question": sfn.JsonPath.string_at("$.question"),
                }
            ),
            result_path="$.agent_result",
            result_selector={
                "answer": sfn.JsonPath.string_at("$.Payload.answer"),
                "safe": sfn.JsonPath.string_at("$.Payload.safe"),
                "narrator_backend": sfn.JsonPath.string_at("$.Payload.trace.narrator_backend"),
            },
            task_timeout=sfn.Timeout.duration(Duration.seconds(25)),
        )
        _add_throttling_retry(invoke_agent_task)

        record_success_task = tasks.LambdaInvoke(
            self,
            "RecordSuccess",
            lambda_function=record_result_handler,
            payload=sfn.TaskInput.from_object(
                {
                    "run_id": sfn.JsonPath.string_at("$.run_id"),
                    "outcome": "SUCCEEDED",
                    "answer": sfn.JsonPath.string_at("$.agent_result.answer"),
                    "safe": sfn.JsonPath.string_at("$.agent_result.safe"),
                    "narrator_backend": sfn.JsonPath.string_at("$.agent_result.narrator_backend"),
                }
            ),
            result_path=sfn.JsonPath.DISCARD,
        )
        _add_throttling_retry(record_success_task)

        record_failure_task = tasks.LambdaInvoke(
            self,
            "RecordFailure",
            lambda_function=record_result_handler,
            payload=sfn.TaskInput.from_object(
                {
                    "run_id": sfn.JsonPath.string_at("$.run_id"),
                    "outcome": "FAILED",
                    "error": sfn.JsonPath.string_at("$.error_info.Cause"),
                }
            ),
            result_path=sfn.JsonPath.DISCARD,
        )
        _add_throttling_retry(record_failure_task)

        record_timeout_task = tasks.LambdaInvoke(
            self,
            "RecordTimeout",
            lambda_function=record_result_handler,
            payload=sfn.TaskInput.from_object(
                {
                    "run_id": sfn.JsonPath.string_at("$.run_id"),
                    "outcome": "TIMED_OUT",
                    "error": sfn.JsonPath.string_at("$.error_info.Cause"),
                }
            ),
            result_path=sfn.JsonPath.DISCARD,
        )
        _add_throttling_retry(record_timeout_task)

        is_timeout_choice = (
            sfn.Choice(self, "IsTimeout")
            .when(sfn.Condition.string_equals("$.error_info.Error", "States.Timeout"), record_timeout_task)
            .otherwise(record_failure_task)
        )

        invoke_agent_task.add_catch(is_timeout_choice, errors=[sfn.Errors.ALL], result_path="$.error_info")

        definition = mark_running_task.next(invoke_agent_task).next(record_success_task)

        self.state_machine = sfn.StateMachine(
            self,
            "AgentRunStateMachine",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.minutes(5),
            state_machine_type=sfn.StateMachineType.STANDARD,
        )

        self.state_machine.grant_start_execution(self.start_run_handler)
        self.state_machine.grant_execution(self.cancel_run_handler, "states:StopExecution")

        self.start_run_handler.add_environment("STATE_MACHINE_ARN", self.state_machine.state_machine_arn)
        self.cancel_run_handler.add_environment("STATE_MACHINE_ARN", self.state_machine.state_machine_arn)

        CfnOutput(self, "StateMachineArn", value=self.state_machine.state_machine_arn)
