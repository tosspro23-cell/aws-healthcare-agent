"""DataStack: persistent state for agent runs.

- DynamoDB table (`RunsTable`): one item per run, keyed by `run_id`. Holds
  compact run metadata (question, answer, safe flag, narrator backend,
  timestamp) for fast lookup -- NOT the full execution trace, which can be
  a few KB of JSON and belongs in S3 instead.
- S3 bucket (`EvidenceBucket`): full JSON execution trace per run, keyed by
  `{run_id}.json` -- every tool call, retrieved chunk, grounded fact, and
  safety-check result, for audit/debugging without bloating the DynamoDB
  item.

Phase 3 (Step Functions orchestration) will layer conditional-write based
terminal-state ownership on top of this same table -- see
`../../docs/AWS_ROADMAP.md`.

Removal policy is deliberately DESTROY + auto-delete: this is a personal
demo/learning project, not a system holding real user data, and leaving
orphaned billed resources around after `cdk destroy` is the wrong default
for that context.
"""

from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_s3 as s3
from constructs import Construct


class DataStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.runs_table = dynamodb.Table(
            self,
            "RunsTable",
            partition_key=dynamodb.Attribute(name="run_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            # Continuous backups (35-day restore window) against an
            # accidental overwrite/delete -- essentially free at this
            # table's write volume, so there's no real tradeoff being
            # made here (flagged by cdk-nag's AwsSolutions-DDB3).
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
        )

        self.evidence_bucket = s3.Bucket(
            self,
            "EvidenceBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
        )
