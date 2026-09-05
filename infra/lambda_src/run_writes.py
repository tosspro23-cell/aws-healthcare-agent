"""Shared DynamoDB conditional-write helper for the SQS-buffered queue
path (`process_job.py`, `reconcile_dlq.py`) -- both need the identical
reserved-keyword-safe UpdateExpression shape for a status-conditioned
write, so it's factored out here rather than duplicated between them.
Not meant as a general-purpose helper beyond these two call sites: the
Step Functions path's Lambdas (`record_result.py`, `cancel_run.py`,
`mark_running.py`) each write their own bespoke version because their
conditions are each a single fixed status check, not a variable
`if_status_in` set.
"""

from __future__ import annotations

import os

import boto3
from botocore.exceptions import ClientError

_RUNS_TABLE_NAME = os.environ["RUNS_TABLE_NAME"]

_dynamodb_resource = None


def _dynamodb():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource


def conditional_status_write(run_id: str, *, if_status_in: tuple[str, ...], **fields: object) -> bool:
    """Returns True if the write happened. Returns False (without
    raising) if the record's current status wasn't one of `if_status_in`
    -- a stale/duplicate delivery, or a race lost against something else
    that already changed the record; that's expected, not an error, so
    the caller should treat it as "nothing more to do here."

    `status` is a DynamoDB reserved keyword -- an UpdateExpression can't
    use it bare. Every field name gets aliased via
    ExpressionAttributeNames unconditionally, not just the ones known
    today to be reserved, so adding a future field can't silently
    reintroduce this same bug for some other reserved word.
    """
    table = _dynamodb().Table(_RUNS_TABLE_NAME)
    update_parts = [f"#{key} = :{key}" for key in fields]
    names = {f"#{key}": key for key in fields}
    values = {f":{key}": value for key, value in fields.items()}

    names["#status"] = "status"
    placeholders = [f":cond_status_{i}" for i in range(len(if_status_in))]
    condition = "#status IN (" + ", ".join(placeholders) + ")"
    for placeholder, allowed_value in zip(placeholders, if_status_in, strict=True):
        values[placeholder] = allowed_value

    try:
        table.update_item(
            Key={"run_id": run_id},
            UpdateExpression="SET " + ", ".join(update_parts),
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return False
