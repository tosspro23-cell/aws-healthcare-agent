"""QueueStack: an SQS-buffered alternative to Step Functions' orchestration
(Phase 3) for the same "run the agent asynchronously" job -- built
specifically as an architecture-comparison experiment, matching the
Azure/Durable-Functions counterpart's own internal queuing.

Where Step Functions leans on explicit, typed retry
(`add_retry(errors=[...])` per task -- see `orchestration_stack.py`) to
survive the account's Lambda concurrency ceiling, this stack leans on
*bounded consumer concurrency*: `POST /jobs` (`enqueue_job.py`) can accept
an arbitrarily large burst of submissions -- SQS itself has no meaningful
concurrency limit on `SendMessage` -- while the actual work
(`process_job.py`, the only place `HealthAgent.ask()` gets called here)
is capped at `max_concurrency` concurrent Lambda invocations by the SQS
event source, regardless of how many messages are sitting in the queue.
That's a fundamentally different resilience shape than retry-with-backoff:
retry can still exhaust its budget under sustained load (see the n=50
result in `../../docs/STRESS_TEST.md`); a bounded consumer queue instead
just takes longer to drain a large burst, without ever throttling.

Reuses `../../DataStack`'s `RunsTable` and the existing
`GET /runs/{run_id}` (`get_run.py`, schema-agnostic) for polling -- no new
table, no new polling endpoint. Reuses `bedrock_grant.py` for the same
scoped `bedrock:InvokeModel` IAM this project's other two paths use.
"""

from pathlib import Path

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_lambda_event_sources as lambda_event_sources
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from stacks.bedrock_grant import grant_bedrock_invoke

# How many ProcessJobHandler invocations may run at once, regardless of
# queue depth -- deliberately well under the account's real Lambda
# concurrency ceiling (10, confirmed via `aws service-quotas` -- see
# docs/STRESS_TEST.md) so this queue can never itself starve every other
# Lambda function in the account of its share of that same shared pool.
_MAX_CONCURRENT_CONSUMERS = 5

# After this many failed delivery attempts (Lambda invocation raised, or
# timed out), SQS stops retrying and moves the message to the DLQ instead
# -- the queue-native equivalent of Step Functions' bounded `max_attempts`.
_MAX_RECEIVE_COUNT = 3


class QueueStack(Stack):
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

        dlq = sqs.Queue(
            self,
            "JobsDLQ",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
        )

        self.queue = sqs.Queue(
            self,
            "JobsQueue",
            # Must comfortably exceed ProcessJobHandler's own Lambda
            # timeout (30s below) -- otherwise SQS could make a message
            # visible to a second consumer again while the first is still
            # legitimately processing it, causing duplicate work.
            visibility_timeout=Duration.seconds(90),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=_MAX_RECEIVE_COUNT, queue=dlq),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
        )

        self.enqueue_job_handler = _lambda.Function(
            self,
            "EnqueueJobHandler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="enqueue_job.handler",
            code=_lambda.Code.from_asset(str(lambda_asset_dir)),
            timeout=Duration.seconds(10),
            environment={"RUNS_TABLE_NAME": runs_table.table_name, "JOBS_QUEUE_URL": self.queue.queue_url},
        )
        # `enqueue_job.py` `put_item`s the initial record and `update_item`s
        # a compensating failure write -- never deletes or batch-writes.
        runs_table.grant(self.enqueue_job_handler, "dynamodb:PutItem", "dynamodb:UpdateItem")
        self.queue.grant_send_messages(self.enqueue_job_handler)

        process_job_handler = _lambda.Function(
            self,
            "ProcessJobHandler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="process_job.handler",
            code=_lambda.Code.from_asset(str(lambda_asset_dir)),
            timeout=Duration.seconds(30),
            memory_size=512,
            environment={"RUNS_TABLE_NAME": runs_table.table_name, "CARE_AGENT_NARRATOR_BACKEND": "bedrock"},
        )
        # `process_job.py` only ever `update_item`s (conditional writes for
        # the RUNNING/terminal transitions).
        runs_table.grant(process_job_handler, "dynamodb:UpdateItem")
        grant_bedrock_invoke(process_job_handler)
        process_job_handler.add_event_source(
            lambda_event_sources.SqsEventSource(self.queue, batch_size=1, max_concurrency=_MAX_CONCURRENT_CONSUMERS)
        )

        CfnOutput(self, "JobsQueueUrl", value=self.queue.queue_url)
        CfnOutput(self, "JobsDLQUrl", value=dlq.queue_url)
