"""CDK-level assertions for FrontendStack -- the S3 + CloudFront hosting
for the built Workbench (see `../stacks/frontend_stack.py`).
"""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template
from stacks.frontend_stack import FrontendStack

_FRONTEND_BUILD_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def _synth_stack() -> Template:
    app = cdk.App()
    stack = FrontendStack(app, "TestFrontendStack", frontend_build_dir=_FRONTEND_BUILD_DIR)
    return Template.from_stack(stack)


def test_bucket_blocks_all_public_access():
    template = _synth_stack()
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            }
        },
    )


def test_cloudfront_distribution_uses_origin_access_control_not_a_public_bucket():
    """The bucket has no public bucket policy or website-hosting config --
    CloudFront reaches it via OAC, confirmed by an OriginAccessControl
    resource actually existing (not just assumed from using the
    `with_origin_access_control` helper)."""
    template = _synth_stack()
    template.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)
    template.resource_count_is("AWS::CloudFront::Distribution", 1)


def test_distribution_redirects_http_to_https():
    template = _synth_stack()
    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": Match.object_like(
                {"DefaultCacheBehavior": Match.object_like({"ViewerProtocolPolicy": "redirect-to-https"})}
            )
        },
    )


def test_spa_routes_fall_back_to_index_html():
    """/callback has no matching object in the bucket -- both 403 (S3's
    response for a missing key behind OAC) and 404 must fall back to
    /index.html with a 200, or the client-side callback route 404s
    instead of loading the app."""
    template = _synth_stack()
    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": Match.object_like(
                {
                    "CustomErrorResponses": Match.array_with(
                        [
                            Match.object_like({"ErrorCode": 403, "ResponseCode": 200, "ResponsePagePath": "/index.html"}),
                            Match.object_like({"ErrorCode": 404, "ResponseCode": 200, "ResponsePagePath": "/index.html"}),
                        ]
                    )
                }
            )
        },
    )
