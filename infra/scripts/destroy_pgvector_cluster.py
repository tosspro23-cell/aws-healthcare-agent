#!/usr/bin/env python3
"""Tears down Stage B's pgvector-on-Aurora-Serverless-v2 experiment
cluster -- the mirror image of `create_pgvector_cluster.py`, and (since
that script uses plain `boto3`, not CDK, per its own docstring) the only
way to remove it; there is no `cdk destroy` for this resource.

Deletes the express-created writer instance first (Aurora requires
cluster members gone before the cluster itself can be deleted), then the
cluster, then confirms via a fresh `describe_db_clusters` call that the
account is clean again -- the same live confirmation this project's
other real-resource teardowns already end with.

Usage:
    AWS_PROFILE=dev python infra/scripts/destroy_pgvector_cluster.py
"""

from __future__ import annotations

import argparse

import boto3
from create_pgvector_cluster import DB_CLUSTER_IDENTIFIER

_INSTANCE_IDENTIFIER = f"{DB_CLUSTER_IDENTIFIER}-instance-1"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    rds = boto3.client("rds", region_name=args.region)

    try:
        rds.describe_db_clusters(DBClusterIdentifier=DB_CLUSTER_IDENTIFIER)
    except rds.exceptions.DBClusterNotFoundFault:
        print(f"{DB_CLUSTER_IDENTIFIER!r} does not exist -- nothing to tear down.")
        return

    print(f"Deleting instance {_INSTANCE_IDENTIFIER!r}...")
    try:
        rds.delete_db_instance(DBInstanceIdentifier=_INSTANCE_IDENTIFIER, SkipFinalSnapshot=True)
        rds.get_waiter("db_instance_deleted").wait(DBInstanceIdentifier=_INSTANCE_IDENTIFIER, WaiterConfig={"Delay": 15, "MaxAttempts": 60})
        print("Instance deleted.")
    except rds.exceptions.DBInstanceNotFoundFault:
        print("Instance already gone.")

    print(f"Deleting cluster {DB_CLUSTER_IDENTIFIER!r}...")
    rds.delete_db_cluster(DBClusterIdentifier=DB_CLUSTER_IDENTIFIER, SkipFinalSnapshot=True)
    rds.get_waiter("db_cluster_deleted").wait(DBClusterIdentifier=DB_CLUSTER_IDENTIFIER, WaiterConfig={"Delay": 15, "MaxAttempts": 60})
    print("Cluster deleted.")

    remaining = rds.describe_db_clusters()["DBClusters"]
    print()
    print(f"Post-teardown confirmation -- aws rds describe-db-clusters now returns {len(remaining)} cluster(s): {remaining}")


if __name__ == "__main__":
    main()
