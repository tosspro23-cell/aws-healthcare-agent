"""FrontendStack: hosts the built Workbench (`frontend/dist`) as a public
static site via S3 + CloudFront.

Every other stack in this project exists to serve API calls; this is the
first one whose entire job is serving the browser app itself. Kept as its
own stack (not folded into `ApiStack`) for the same reason every other
piece of this project is split this way: it can be deployed/torn down
independently, and its own CloudFormation changeset never touches
anything API-related.

**Deliberately a two-pass deployment**: this stack's CloudFront domain
isn't known until after it's first deployed, but `AuthStack`'s Cognito
App Client and `ApiStack`'s CORS config both need that exact domain
registered before a browser served from it can complete a real login or
call the API. `app.py` handles this by threading the previous deploy's
known CloudFront domain in in as a parameter (see its own docstring) --
first deploy leaves it unregistered (the site loads, sign-in redirects
correctly to Cognito, but Cognito rejects the callback since that URL
isn't registered yet); the second deploy (after `cdk deploy` prints this
stack's URL) registers it and everything works end to end.

The bucket itself stays private -- no public bucket policy, no static
website hosting endpoint -- CloudFront reaches it via Origin Access
Control (OAC), the modern replacement for the older Origin Access
Identity pattern. `error_responses` maps both 403 (S3's response for a
missing key on a private bucket) and 404 to a 200 serving `/index.html`,
so a path this SPA handles client-side (`/callback`) -- which has no
matching object in the bucket -- still loads the app instead of a raw S3
error page.
"""

from pathlib import Path

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3_deployment
from constructs import Construct


class FrontendStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        frontend_build_dir: Path,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        bucket = s3.Bucket(
            self,
            "WorkbenchBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        self.distribution = cloudfront.Distribution(
            self,
            "WorkbenchDistribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            error_responses=[
                cloudfront.ErrorResponse(http_status=403, response_http_status=200, response_page_path="/index.html"),
                cloudfront.ErrorResponse(http_status=404, response_http_status=200, response_page_path="/index.html"),
            ],
        )

        s3_deployment.BucketDeployment(
            self,
            "DeployWorkbench",
            sources=[s3_deployment.Source.asset(str(frontend_build_dir))],
            destination_bucket=bucket,
            distribution=self.distribution,
            distribution_paths=["/*"],
        )

        self.url = f"https://{self.distribution.distribution_domain_name}"
        CfnOutput(self, "WorkbenchUrl", value=self.url)
