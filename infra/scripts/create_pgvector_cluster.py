#!/usr/bin/env python3
"""Creates Stage B's pgvector-on-Aurora-Serverless-v2 experiment cluster
directly via `boto3`, not CDK/CloudFormation -- and documents, in code
and in its own real failure, exactly why Stage B stopped here rather
than completing the original load/query/evidence pipeline. See
`docs/DECISIONS.md` for the full narrative and the security/isolation
comparison against the VPC-based design this replaced.

**Why not CDK**: the first real deploy attempt via a CDK `DatabaseCluster`
failed live: `CREATE_FAILED ... "To use Aurora clusters with free plan
accounts you need to set WithExpressConfiguration."` This AWS account is
classified as a "free plan account" (AWS's own wording; its own error
message says upgrading the account plan removes the restriction) and
requires Aurora clusters be created via RDS's own
`WithExpressConfiguration=True` API parameter -- confirmed directly that
CloudFormation's `AWS::RDS::DBCluster` resource type has no such
property anywhere in its full ~60-property schema, so no CDK/CFN
construct can satisfy this on this account.

**Why no VPC either**: a follow-up probe (`WithExpressConfiguration=True`
with no other parameters) revealed this account's Express Configuration
creates a cluster with `VPCNetworkingEnabled: False` and
`InternetAccessGatewayEnabled: True` -- a VPC-less networking mode with
a real, public `*.rds.amazonaws.com` endpoint and IAM database
authentication enabled. Passing an explicit custom VPC subnet
group/security group alongside `WithExpressConfiguration=True` failed
live: `InvalidParameterCombination: Amazon RDS can't associate a VPC
because Internet Access Gateway is enabled.` So there is no VPC to build
for this experiment on this account at all.

**Four more real, live-discovered parameter constraints** (found by
trying, not by reading documentation up front) -- `EngineVersion` can't
be specified alongside `WithExpressConfiguration=True` (Express picks
its own, 17.7 as observed live -- still far above any documented
pgvector minimum); neither can `DatabaseName` (create it after, via SQL
-- see below); neither can `ManageMasterUserPassword` at create time
(apply it via a follow-up `modify_db_cluster` instead, which has no such
restriction). All three are worked around below.

**Where this actually stops, confirmed live, not assumed**: even after
`modify_db_cluster(EnableHttpEndpoint=True)` reports success and the
cluster returns to `available`, a direct `rds-data.execute_statement`
call against this cluster fails with `HttpEndpointNotEnabledException`
-- reproduced after multiple retries and a full instance reboot, not a
one-off timing issue. This account's VPC-less, Internet-Access-Gateway
clusters do not support RDS Data API at all; the real supported
connectivity path is standard PostgreSQL wire protocol against the
public endpoint, authenticated via IAM (`rds.generate_db_auth_token`) or
the managed master password -- a materially different, and materially
less isolated, architecture than this project's own VPC-isolated,
Data-API-only design principle (see `docs/DECISIONS.md`'s security
comparison). Continuing down that path (a new `psycopg2`-style
dependency, a public network dependency this project has otherwise
avoided everywhere) was a deliberate choice *not* to make -- this script
still creates a real, working cluster and gets as far as confirming that
exact boundary, which is the actual deliverable of this stage: a
precise, live-verified account constraint, not a working pgvector
comparison.

Not part of pytest or CI -- real, deliberate, real-cost calls, same tier
as `stress_test.py`/`get_dev_token.py`. Pair with
`destroy_pgvector_cluster.py` to tear this down -- do not leave it
running.

Usage:
    AWS_PROFILE=dev python infra/scripts/create_pgvector_cluster.py
"""

from __future__ import annotations

import argparse
import json

import boto3

DB_CLUSTER_IDENTIFIER = "care-agent-vector-experiment"
MASTER_USERNAME = "vector_admin"
# Floor stays minimal (idle cost is 0.5 ACU-hours); ceiling gives a
# one-time load burst headroom to scale up briefly without throttling.
# Explicit, not Express Configuration's own default (0/4 with a
# 300-second auto-pause) -- auto-pause is deliberately not wanted here:
# a short-lived experiment with no idle window worth optimizing, and
# auto-pause only adds cold-resume latency as a needless failure mode.
SERVERLESS_V2_SCALING = {"MinCapacity": 0.5, "MaxCapacity": 2.0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    rds = boto3.client("rds", region_name=args.region)

    print(f"Creating cluster {DB_CLUSTER_IDENTIFIER!r} (WithExpressConfiguration=True, no VPC, no explicit engine version)...")
    rds.create_db_cluster(
        DBClusterIdentifier=DB_CLUSTER_IDENTIFIER,
        Engine="aurora-postgresql",
        WithExpressConfiguration=True,
        ServerlessV2ScalingConfiguration=SERVERLESS_V2_SCALING,
        MasterUsername=MASTER_USERNAME,
        DeletionProtection=False,
        BackupRetentionPeriod=1,
    )

    print("Waiting for the express-created writer instance to become available...")
    rds.get_waiter("db_instance_available").wait(
        DBInstanceIdentifier=f"{DB_CLUSTER_IDENTIFIER}-instance-1", WaiterConfig={"Delay": 15, "MaxAttempts": 60}
    )

    print("Converting to a Secrets-Manager-managed master password (can't be set at create time -- see this file's own docstring)...")
    rds.modify_db_cluster(DBClusterIdentifier=DB_CLUSTER_IDENTIFIER, ManageMasterUserPassword=True, ApplyImmediately=True)
    rds.get_waiter("db_cluster_available").wait(DBClusterIdentifier=DB_CLUSTER_IDENTIFIER, WaiterConfig={"Delay": 10, "MaxAttempts": 30})

    print("Attempting to enable RDS Data API...")
    rds.modify_db_cluster(DBClusterIdentifier=DB_CLUSTER_IDENTIFIER, EnableHttpEndpoint=True, ApplyImmediately=True)
    rds.get_waiter("db_cluster_available").wait(DBClusterIdentifier=DB_CLUSTER_IDENTIFIER, WaiterConfig={"Delay": 10, "MaxAttempts": 30})

    described = rds.describe_db_clusters(DBClusterIdentifier=DB_CLUSTER_IDENTIFIER)["DBClusters"][0]
    cluster_arn = described["DBClusterArn"]
    secret_arn = described["MasterUserSecret"]["SecretArn"]
    http_endpoint_enabled = described["HttpEndpointEnabled"]

    print()
    print(f"ClusterArn: {cluster_arn}")
    print(f"SecretArn:  {secret_arn}")
    print(f"HttpEndpointEnabled (per describe_db_clusters): {http_endpoint_enabled}")
    print(f"ServerlessV2ScalingConfiguration: {json.dumps(described['ServerlessV2ScalingConfiguration'])}")

    # The flag above reports True right after the modify call returns, but
    # a real Data API call is the only real proof -- prove it fresh on
    # every run rather than resting on an earlier finding. Confirmed live
    # (including after a full instance reboot) that this fails every time
    # on this account's Express Configuration clusters, not a timing
    # fluke -- see this module's own docstring for the full narrative.
    print()
    print("Confirming with a real rds-data.execute_statement call (the only real proof, not just the flag above)...")
    rds_data = boto3.client("rds-data", region_name=args.region)
    try:
        rds_data.execute_statement(resourceArn=cluster_arn, secretArn=secret_arn, database="postgres", sql="SELECT 1;")
        print("Data API call succeeded -- unexpected on this account; proceed to build the load/query pipeline after all.")
    except rds_data.exceptions.HttpEndpointNotEnabledException as exc:
        print(f"Data API call failed as expected: {type(exc).__name__}: {exc}")
        print()
        print("This is Stage B's real stopping point on this account -- see docs/DECISIONS.md for the full finding and the")
        print("security/isolation reasoning for not pursuing a public-endpoint + IAM-auth workaround instead.")

    print()
    print("Run `AWS_PROFILE=dev python infra/scripts/destroy_pgvector_cluster.py` to tear this down when you're done inspecting it.")


if __name__ == "__main__":
    main()
