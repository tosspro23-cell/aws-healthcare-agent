"""Suppressions for cdk-nag's `AwsSolutionsChecks` (applied in `app.py`,
`if __name__ == "__main__"` only -- never inside `build_app()` itself, so
existing tests that call `build_app()` directly are unaffected).

Every suppression here was reviewed against the actual synthesized
CloudFormation, not applied speculatively to make noise go away -- see
each one's `reason` (also visible in the deployed stack's own template
metadata, not just in this source file) and `docs/DECISIONS.md`'s entry
for the full investigation. A finding not listed here is meant to fail
`cdk synth` -- that's the actual gate this module exists to make
possible, not just an initial cleanup pass.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Stack
from cdk_nag import NagSuppressions, RegexAppliesTo

# Every Lambda-bearing stack gets the identical CDK-standard managed
# policy (basic CloudWatch Logs write, attached automatically by every
# `aws_lambda.Function` unless a custom log-retention/role setup opts
# out) and, as of this review, the same runtime-version finding.
_LAMBDA_STACK_SUPPRESSIONS: list[dict[str, Any]] = [
    {
        "id": "AwsSolutions-IAM4",
        "reason": (
            "AWSLambdaBasicExecutionRole is the CDK-standard minimal CloudWatch Logs write policy attached "
            "automatically to every Lambda function in this app. Recreating it as a hand-written custom policy "
            "would grant the exact same three actions (CreateLogGroup/CreateLogStream/PutLogEvents) with no "
            "security improvement -- pure boilerplate."
        ),
        "applies_to": ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"],
    },
    {
        "id": "AwsSolutions-L1",
        "reason": (
            "PYTHON_3_13 is the most recent Lambda runtime this project has actually confirmed is a real, "
            "generally-available AWS Lambda runtime (checked directly, not assumed) at the time of this review. "
            "aws-cdk-lib also defines a PYTHON_3_14 enum constant, which is what makes cdk-nag consider 3.13 "
            "outdated -- but a CDK enum constant existing is not the same claim as AWS Lambda actually supporting "
            "that runtime in production yet, and deploying against an unconfirmed runtime risked a real deploy "
            "failure to chase a lint finding. Revisit once 3.14 is confirmed GA."
        ),
    },
]

# The evidence bucket's own object-key-suffix wildcard (`bucket-arn/*`,
# plus the matching `s3:Abort*` for multipart uploads) is the correct,
# minimal way to grant "write any object under this bucket" -- there is
# no way to scope `s3:PutObject`/`s3:GetObject` etc. to a bucket without
# either a trailing `/*` or naming every future key in advance. This
# project's own `tests/iam_assertions.py` already encodes this same
# distinction (a bucket-object-suffix wildcard is not treated the same
# as a genuinely broad `Resource: "*"`); cdk-nag's generic rule doesn't
# have that context, so it flags every wildcard it sees regardless of
# shape.
_EVIDENCE_BUCKET_OBJECT_WILDCARD: dict[str, Any] = {
    "id": "AwsSolutions-IAM5",
    "reason": (
        "The evidence bucket's object-key-suffix wildcard (bucket-arn/*) and the matching s3:Abort* grant are "
        "the minimal way to grant per-object access within a bucket -- there is no way to scope this without a "
        "trailing wildcard. Confirmed via the synthesized template, not assumed."
    ),
    "applies_to": ["Action::s3:Abort*", RegexAppliesTo(regex=r"/^Resource::.*EvidenceBucket.*\/\*$/")],
}


def apply_nag_suppressions(
    *,
    auth_stack: Stack,
    data_stack: Stack,
    orchestration_stack: Stack,
    queue_stack: Stack,
    api_stack: Stack,
    frontend_stack: Stack,
    cicd_stack: Stack,
) -> None:
    NagSuppressions.add_stack_suppressions(
        auth_stack,
        [
            {
                "id": "AwsSolutions-COG2",
                "reason": (
                    "Synthetic-data demo with self-sign-up disabled and accounts created only via AdminCreateUser "
                    "-- MFA would add real friction for zero benefit against this project's actual threat model. "
                    "Would enable if this ever held real user data."
                ),
            },
            {
                "id": "AwsSolutions-COG8",
                "reason": (
                    "Plus tier's advanced security features (compromised-credential detection, adaptive "
                    "authentication) bill per MAU -- a real ongoing cost with no corresponding benefit for a "
                    "single-demo-account project with no real user data at risk."
                ),
            },
        ],
    )

    NagSuppressions.add_stack_suppressions(
        data_stack,
        [
            {
                "id": "AwsSolutions-S1",
                "reason": (
                    "Server access logs on the evidence bucket would roughly double its storage footprint "
                    "logging access to synthetic demo data, for no real audit need at this project's scale. "
                    "Point-in-time recovery (enabled on RunsTable) is the durability control that actually "
                    "matters for this project's own data; would add access logging if this held real user data."
                ),
            },
        ],
    )

    for lambda_stack in (orchestration_stack, queue_stack, api_stack):
        NagSuppressions.add_stack_suppressions(lambda_stack, _LAMBDA_STACK_SUPPRESSIONS)
        NagSuppressions.add_stack_suppressions(lambda_stack, [_EVIDENCE_BUCKET_OBJECT_WILDCARD])

    NagSuppressions.add_stack_suppressions(
        orchestration_stack,
        [
            {
                "id": "AwsSolutions-IAM5",
                "reason": (
                    "Three distinct, unavoidable CDK-standard patterns, each confirmed directly against the "
                    "synthesized template: (1) the Step Function's own Lambda-invoke grants append ':*' to allow "
                    "any function version/alias, CDK's standard shape for grant_invoke-style permissions; (2) "
                    "'states:...execution:...:*' lets the Lambda interact with any execution of this one specific "
                    "state machine, since execution names are only known at runtime; (3) the 'Resource: *' "
                    "statement is for logs:CreateLogDelivery/DeleteLogDelivery (Step Functions' own CloudWatch "
                    "Logs destination wiring) and xray:PutTraceSegments/PutTelemetryRecords -- both AWS APIs that "
                    "do not support resource-level scoping at all, per AWS's own IAM reference."
                ),
                "applies_to": [
                    RegexAppliesTo(regex=r"/^Resource::.*(MarkRunningHandler|AgentTaskHandler|RecordResultHandler).*:\*$/"),
                    RegexAppliesTo(regex=r"/^Resource::arn:.*:states:.*:execution:.*$/"),
                    "Resource::*",
                ],
            },
        ],
    )

    NagSuppressions.add_stack_suppressions(
        frontend_stack,
        [
            *_LAMBDA_STACK_SUPPRESSIONS,
            {
                "id": "AwsSolutions-IAM5",
                "reason": (
                    "Every wildcard here belongs to CDK's own BucketDeployment L3 construct (its auto-generated "
                    "sync/invalidation Lambda), not to this project's own IAM grants: read access to the CDK "
                    "asset-staging bucket, write access to the Workbench bucket (the same object-key-suffix "
                    "pattern already justified above), and cloudfront:CreateInvalidation/GetInvalidation, which "
                    "the construct hardcodes to Resource:'*' regardless of how it's invoked -- confirmed directly "
                    "against the synthesized template, not assumed. Replacing BucketDeployment with a hand-rolled "
                    "equivalent to narrow this further is out of proportion to the risk it would close."
                ),
                "applies_to": [
                    "Action::s3:Abort*",
                    "Action::s3:DeleteObject*",
                    "Action::s3:GetBucket*",
                    "Action::s3:GetObject*",
                    "Action::s3:List*",
                    RegexAppliesTo(regex=r"/^Resource::.*$/"),
                ],
            },
            {
                "id": "AwsSolutions-S1",
                "reason": (
                    "Same reasoning as the evidence bucket (DataStack): access logs on a bucket serving only "
                    "public, non-sensitive built frontend assets would add cost with no real audit value at this "
                    "project's scale."
                ),
            },
            {
                "id": "AwsSolutions-CFR1",
                "reason": (
                    "No geographic restriction requirement -- this is a public demo Workbench, not a service "
                    "with jurisdiction-specific access rules."
                ),
            },
            {
                "id": "AwsSolutions-CFR2",
                "reason": (
                    "AWS WAF has an ongoing per-web-ACL cost that isn't warranted for a portfolio/demo project "
                    "with no real traffic to defend and Cognito auth already gating every API call the site can "
                    "make. Would add if this ever saw real production traffic."
                ),
            },
            {
                "id": "AwsSolutions-CFR3",
                "reason": (
                    "CloudFront access logs would need a second, dedicated S3 bucket purely to receive them -- "
                    "disproportionate infrastructure for a single-page demo app with no real audit requirement. "
                    "Server-side request handling is already covered by ApiStack's own access-logged API Gateway "
                    "stage; this distribution only serves static assets."
                ),
            },
            {
                "id": "AwsSolutions-CFR4",
                "reason": (
                    "Confirmed directly via cdk synth (not assumed): CloudFront's minimum TLS protocol version is "
                    "only configurable with a custom domain + ACM certificate -- the shared *.cloudfront.net "
                    "certificate this project uses has its security policy fixed at TLSv1 regardless of what's "
                    "requested. Registering a real custom domain solely to raise this floor is out of scope for "
                    "a demo project with no real domain of its own."
                ),
            },
        ],
    )

    NagSuppressions.add_stack_suppressions(
        api_stack,
        [
            {
                "id": "AwsSolutions-APIG1",
                "reason": (
                    "Access logging IS actually configured on this HTTP API's stage (see api_stack.py's "
                    "AccessLogSettings override, confirmed present in the synthesized template) -- this rule "
                    "appears not to recognize access logging configured via a raw property override on an "
                    "HttpApi v2 stage the way it does for a REST API v1 stage's L2-native access-logging props."
                ),
            },
        ],
    )

    NagSuppressions.add_stack_suppressions(
        cicd_stack,
        [
            {
                "id": "AwsSolutions-IAM5",
                "reason": (
                    "cdk-nag flags any trailing '/*' as a wildcard, but this is a stack-ID wildcard, not a "
                    "resource-type-wide one: each ARN names one exact stack (CareAgentAuthStack or "
                    "CareAgentApiStack) and the trailing '/*' only covers that stack's own CloudFormation "
                    "stack-id suffix, which changes on every replacement -- there is no way to grant "
                    "DescribeStacks on a specific stack without it. Confirmed via the synthesized template: "
                    "exactly two resources, each naming one of the two stacks ci.yml's own 'write "
                    "frontend/.env.local' step actually reads, not a wildcard across the account."
                ),
                "applies_to": [
                    RegexAppliesTo(regex=r"/^Resource::.*stack\/CareAgentAuthStack\/\*$/"),
                    RegexAppliesTo(regex=r"/^Resource::.*stack\/CareAgentApiStack\/\*$/"),
                ],
            },
        ],
    )
