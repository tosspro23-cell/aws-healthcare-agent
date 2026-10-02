"""Tests for infra/lambda_src/run_reads.py against a moto-mocked DynamoDB
table and S3 bucket -- no real AWS account, no network call.
"""

import json
import os

import boto3
import run_reads  # noqa: E402 -- import after conftest sets sys.path/env vars
from moto import mock_aws

_OWNER = "cognito-sub-caller-1"
_OTHER_OWNER = "cognito-sub-caller-2"


def _aws_resources():
    dynamodb = boto3.client("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=os.environ["RUNS_TABLE_NAME"],
        KeySchema=[{"AttributeName": "run_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "run_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=os.environ["EVIDENCE_BUCKET_NAME"])


_FACT = {"claim": "HDL-C = 47 mg/dL", "source_type": "bloodwork", "source_ref": "x", "numeric_values": [47.0], "unit": "mg/dL"}


@mock_aws
def test_happy_path_returns_the_runs_grounded_facts():
    _aws_resources()
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(os.environ["RUNS_TABLE_NAME"])
    table.put_item(Item={"run_id": "r1", "status": "SUCCEEDED", "owner_sub": _OWNER, "execution_type": "SYNC"})
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=os.environ["EVIDENCE_BUCKET_NAME"],
        Key="r1.json",
        Body=json.dumps({"grounded_facts": [_FACT]}).encode("utf-8"),
        ContentType="application/json",
    )

    facts = run_reads.fetch_prior_grounded_facts(["r1"], _OWNER)
    assert facts == [_FACT]


@mock_aws
def test_run_owned_by_a_different_caller_contributes_nothing():
    """The actual security property this module exists for: a run_id that
    doesn't belong to the caller must never be read, not even to confirm
    it exists -- mirrors get_run.py's own ownership check."""
    _aws_resources()
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(os.environ["RUNS_TABLE_NAME"])
    table.put_item(Item={"run_id": "r1", "status": "SUCCEEDED", "owner_sub": _OTHER_OWNER, "execution_type": "SYNC"})
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=os.environ["EVIDENCE_BUCKET_NAME"],
        Key="r1.json",
        Body=json.dumps({"grounded_facts": [_FACT]}).encode("utf-8"),
        ContentType="application/json",
    )

    assert run_reads.fetch_prior_grounded_facts(["r1"], _OWNER) == []


@mock_aws
def test_missing_run_id_is_skipped_not_an_error():
    _aws_resources()
    assert run_reads.fetch_prior_grounded_facts(["never-existed"], _OWNER) == []


@mock_aws
def test_run_with_no_evidence_object_yet_is_skipped_not_an_error():
    """A run still in progress (or one that failed before writing
    evidence) has no S3 object yet -- the common, expected case, not an
    error."""
    _aws_resources()
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(os.environ["RUNS_TABLE_NAME"])
    table.put_item(Item={"run_id": "r1", "status": "RUNNING", "owner_sub": _OWNER, "execution_type": "SYNC"})

    assert run_reads.fetch_prior_grounded_facts(["r1"], _OWNER) == []


@mock_aws
def test_empty_run_ids_returns_empty_with_no_aws_calls():
    # Deliberately no table/bucket created -- a real AWS call here would
    # raise, proving the early-return path never makes one.
    assert run_reads.fetch_prior_grounded_facts([], _OWNER) == []


@mock_aws
def test_caps_at_max_prior_run_ids():
    _aws_resources()
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(os.environ["RUNS_TABLE_NAME"])
    s3 = boto3.client("s3", region_name="us-east-1")
    run_ids = [f"r{i}" for i in range(run_reads._MAX_PRIOR_RUN_IDS + 5)]
    for run_id in run_ids:
        table.put_item(Item={"run_id": run_id, "status": "SUCCEEDED", "owner_sub": _OWNER, "execution_type": "SYNC"})
        s3.put_object(
            Bucket=os.environ["EVIDENCE_BUCKET_NAME"],
            Key=f"{run_id}.json",
            Body=json.dumps({"grounded_facts": [_FACT]}).encode("utf-8"),
            ContentType="application/json",
        )

    facts = run_reads.fetch_prior_grounded_facts(run_ids, _OWNER)
    assert len(facts) == run_reads._MAX_PRIOR_RUN_IDS
