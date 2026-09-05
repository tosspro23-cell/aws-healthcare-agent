"""CDK-level assertions for FrontendStack -- the S3 + CloudFront hosting
for the built Workbench (see `../stacks/frontend_stack.py`).
"""

from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template
from build_frontend_asset import build_frontend_asset
from stacks.frontend_stack import FrontendStack


@pytest.fixture(scope="module")
def frontend_build_dir() -> Path:
    # `BucketDeployment`'s underlying asset staging fails synth outright
    # if this directory doesn't exist yet -- unlike a moto-mocked AWS
    # call, there's no way to construct this stack against a directory
    # that isn't actually there. A fresh checkout (CI included) has no
    # frontend/dist until something builds it; relying on some *other*
    # test (e.g. one that calls build_app()) to have run first and left
    # it behind is exactly the kind of test-order-dependent fragility
    # this project avoids elsewhere, so this module builds it itself --
    # once per module (an `npm run build` per test would be wasteful),
    # not assumed to persist from an unrelated test.
    return build_frontend_asset()


def _synth_stack(frontend_build_dir: Path) -> Template:
    app = cdk.App()
    stack = FrontendStack(app, "TestFrontendStack", frontend_build_dir=frontend_build_dir)
    return Template.from_stack(stack)


def test_bucket_blocks_all_public_access(frontend_build_dir):
    template = _synth_stack(frontend_build_dir)
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


def test_cloudfront_distribution_uses_origin_access_control_not_a_public_bucket(frontend_build_dir):
    """The bucket has no public bucket policy or website-hosting config --
    CloudFront reaches it via OAC, confirmed by an OriginAccessControl
    resource actually existing (not just assumed from using the
    `with_origin_access_control` helper)."""
    template = _synth_stack(frontend_build_dir)
    template.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)
    template.resource_count_is("AWS::CloudFront::Distribution", 1)


def test_distribution_redirects_http_to_https(frontend_build_dir):
    template = _synth_stack(frontend_build_dir)
    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": Match.object_like(
                {"DefaultCacheBehavior": Match.object_like({"ViewerProtocolPolicy": "redirect-to-https"})}
            )
        },
    )


def test_spa_routes_fall_back_to_index_html(frontend_build_dir):
    """/callback has no matching object in the bucket -- both 403 (S3's
    response for a missing key behind OAC) and 404 must fall back to
    /index.html with a 200, or the client-side callback route 404s
    instead of loading the app."""
    template = _synth_stack(frontend_build_dir)
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
