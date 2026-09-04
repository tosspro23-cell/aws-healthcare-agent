"""Shared IAM grant for any Lambda that runs `BedrockNarrator`.

Scoped precisely to `bedrock:InvokeModel` on the specific cross-region
inference profile `care_agent.narrator.bedrock_narrator.DEFAULT_MODEL_ID`
uses, plus the underlying foundation-model ARNs that profile routes
requests to. Confirmed via `aws bedrock get-inference-profile
--inference-profile-identifier us.anthropic.claude-haiku-4-5-20251001-v1:0`
against the real account: a cross-region profile needs permission on
*both* the profile resource and the on-demand foundation-model resources
in every region it can route a request to (us-east-1, us-east-2,
us-west-2 for this "us." profile) -- the profile ARN alone is not
sufficient. No broad `bedrock:*` or wildcard-resource permission is
granted; see `docs/DECISIONS.md`.
"""

from aws_cdk import Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda

_MODEL_ID = "anthropic.claude-haiku-4-5-20251001-v1:0"
_PROFILE_REGION = "us-east-1"
_ROUTED_REGIONS = ("us-east-1", "us-east-2", "us-west-2")


def grant_bedrock_invoke(fn: _lambda.Function) -> None:
    stack = Stack.of(fn)
    resources = [
        f"arn:aws:bedrock:{_PROFILE_REGION}:{stack.account}:inference-profile/us.{_MODEL_ID}",
        *(f"arn:aws:bedrock:{region}::foundation-model/{_MODEL_ID}" for region in _ROUTED_REGIONS),
    ]
    fn.add_to_role_policy(
        iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=resources,
        )
    )
