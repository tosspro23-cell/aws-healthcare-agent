"""Assertions against QueueStack's synthesized CloudFormation -- the
SQS-buffered alternative to Step Functions orchestration (see
`../stacks/queue_stack.py` for why this exists).
"""

import json
from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template
from stacks.data_stack import DataStack
from stacks.queue_stack import QueueStack

from tests.iam_assertions import assert_no_overly_broad_iam_policy

_LAMBDA_ASSET_DIR = Path(__file__).resolve().parent.parent / "lambda_src"


def _synth_queue_stack():
    app = cdk.App()
    data_stack = DataStack(app, "TestDataStackQ")
    queue_stack = QueueStack(
        app,
        "TestQueueStack",
        runs_table=data_stack.runs_table,
        evidence_bucket=data_stack.evidence_bucket,
        lambda_asset_dir=_LAMBDA_ASSET_DIR,
    )
    return Template.from_stack(queue_stack)


def test_queue_and_dlq_both_exist():
    template = _synth_queue_stack()
    template.resource_count_is("AWS::SQS::Queue", 2)


def test_queue_has_dead_letter_redrive_policy():
    template = _synth_queue_stack()
    template.has_resource_properties(
        "AWS::SQS::Queue",
        {"RedrivePolicy": {"deadLetterTargetArn": Match.any_value(), "maxReceiveCount": 3}},
    )


def test_queue_visibility_timeout_exceeds_consumer_lambda_timeout():
    """Regression guard: if this ever drifts below ProcessJobHandler's own
    Lambda timeout, a message could become visible to a second consumer
    while the first is still legitimately processing it, causing duplicate
    (and, since Bedrock is billed per token, wasted-money) double work."""
    template = _synth_queue_stack()
    functions = template.find_resources("AWS::Lambda::Function", {"Properties": {"Handler": "process_job.handler"}})
    assert len(functions) == 1
    consumer_timeout = next(iter(functions.values()))["Properties"]["Timeout"]

    queues = template.find_resources("AWS::SQS::Queue")
    # The main queue is the one with a RedrivePolicy pointing at the DLQ (not the DLQ itself).
    main_queue = next(v for v in queues.values() if "RedrivePolicy" in v["Properties"])
    assert main_queue["Properties"]["VisibilityTimeout"] > consumer_timeout


def test_consumer_lambda_has_bounded_max_concurrency():
    """The whole point of this stack: ProcessJobHandler's own concurrency
    is capped independent of queue depth, well under the account's real
    Lambda concurrency ceiling (10 -- see docs/STRESS_TEST.md)."""
    template = _synth_queue_stack()
    template.has_resource_properties(
        "AWS::Lambda::EventSourceMapping",
        {"BatchSize": 1, "ScalingConfig": {"MaximumConcurrency": 5}},
    )


def test_consumer_lambda_uses_python312_runtime_and_expected_handler():
    template = _synth_queue_stack()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Runtime": "python3.12", "Handler": "process_job.handler"},
    )


def test_enqueue_lambda_uses_python312_runtime_and_expected_handler():
    template = _synth_queue_stack()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Runtime": "python3.12", "Handler": "enqueue_job.handler"},
    )


def test_no_iam_policy_uses_wildcard_resource():
    assert_no_overly_broad_iam_policy(_synth_queue_stack())


def test_reconcile_dlq_lambda_uses_python312_runtime_and_expected_handler():
    template = _synth_queue_stack()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Runtime": "python3.12", "Handler": "reconcile_dlq.handler"},
    )


def test_reconcile_dlq_lambda_is_triggered_by_the_dlq_not_the_main_queue():
    """Regression guard for the reconciliation fix: an independent review
    found that a message exceeding process_job.py's max delivery attempts
    left its run_id stuck forever, since nothing was ever triggered by
    the DLQ itself. Confirms the new handler's event source is the DLQ's
    ARN, not the main queue's."""
    template = _synth_queue_stack()
    queues = template.find_resources("AWS::SQS::Queue")
    dlq_logical_id = next(k for k, v in queues.items() if "RedrivePolicy" not in v["Properties"])

    mappings = template.find_resources(
        "AWS::Lambda::EventSourceMapping",
        {"Properties": {"BatchSize": 1, "ScalingConfig": Match.absent()}},
    )
    assert len(mappings) == 1
    event_source_arn = next(iter(mappings.values()))["Properties"]["EventSourceArn"]
    assert dlq_logical_id in json.dumps(event_source_arn)
