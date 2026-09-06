"""CiCdStack: lets GitHub Actions deploy this app with no long-lived AWS
credentials stored as a repository secret.

GitHub's OIDC provider issues each workflow run a short-lived token; this
stack creates the AWS side of that trust -- an OIDC identity provider
plus a role GitHub Actions can assume, scoped by the token's own `sub`
claim to *exactly* one thing, matched with `StringEquals` (not
`StringLike`).

That one thing is the GitHub *environment*, not the branch ref --
`repo:<org>/<repo>:environment:<name>`, not
`repo:<org>/<repo>:ref:refs/heads/main`. Tried the ref-based claim first
(the shape most OIDC-to-AWS guides lead with) and it failed live with
`Not authorized to perform sts:AssumeRoleWithWebIdentity` on the very
first real deploy attempt: GitHub replaces the `sub` claim's shape
entirely once a job references `environment:` (as `ci.yml`'s `deploy`
job does, for its own required-reviewer approval gate) -- confirmed by
reading the actual error and GitHub's own OIDC claims documentation, not
assumed from how the ref-based form is usually presented. This ties two
independent controls together usefully, not just accidentally: the
*only* way to reach this role is a job running under the `production`
environment specifically, which is the exact same environment gated by
a human's approval -- an attacker who somehow got a workflow onto `main`
still couldn't assume this role without also clearing that same
approval gate.

The role itself carries almost no direct permission of its own -- only
`sts:AssumeRole` on the CDK bootstrap's own roles (deploy,
file-publishing, image-publishing, lookup), the exact same roles
`cdk deploy` already uses for a human running it locally against this
account (already bootstrapped with the default `hnb659fds` qualifier --
confirmed via `cdk bootstrap`'s own CloudFormation stack, not assumed).
This stack doesn't widen what those roles can do; it only adds a second,
narrowly-scoped way to reach them, so the actual deploy permissions
continue to live exactly where they already lived.
"""

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

_GITHUB_OIDC_HOST = "token.actions.githubusercontent.com"
_STS_AUDIENCE = "sts.amazonaws.com"
# Matches this account's actual `cdk bootstrap` qualifier (confirmed live
# via the CDKToolkit stack's own parameters, not assumed to be the
# default) -- would need updating here if this account were ever
# re-bootstrapped with a custom `--qualifier`.
_CDK_QUALIFIER = "hnb659fds"


class CiCdStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        github_repo: str = "tosspro23-cell/aws-healthcare-agent",
        deploy_environment: str = "production",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        provider = iam.OpenIdConnectProvider(self, "GitHubOidcProvider", url=f"https://{_GITHUB_OIDC_HOST}", client_ids=[_STS_AUDIENCE])

        bootstrap_role_arns = [
            f"arn:aws:iam::{self.account}:role/cdk-{_CDK_QUALIFIER}-{role}-{self.account}-{self.region}"
            for role in ("deploy-role", "file-publishing-role", "image-publishing-role", "lookup-role")
        ]

        deploy_role = iam.Role(
            self,
            "GitHubActionsDeployRole",
            assumed_by=iam.WebIdentityPrincipal(
                provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        f"{_GITHUB_OIDC_HOST}:aud": _STS_AUDIENCE,
                        f"{_GITHUB_OIDC_HOST}:sub": f"repo:{github_repo}:environment:{deploy_environment}",
                    },
                },
            ),
            description=f"Assumed by GitHub Actions (OIDC) -- only {github_repo}'s {deploy_environment!r} environment can assume this.",
        )
        # Not grant_read_write_data-style convenience: this is the exact
        # action needed (assume the bootstrap roles), on exactly the four
        # bootstrap role ARNs, nothing else -- the same scoping discipline
        # this project applies to every other IAM grant.
        deploy_role.add_to_policy(iam.PolicyStatement(actions=["sts:AssumeRole"], resources=bootstrap_role_arns))

        CfnOutput(self, "GitHubActionsDeployRoleArn", value=deploy_role.role_arn)
