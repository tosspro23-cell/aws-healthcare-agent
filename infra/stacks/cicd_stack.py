"""CiCdStack: lets GitHub Actions deploy this app with no long-lived AWS
credentials stored as a repository secret.

GitHub's OIDC provider issues each workflow run a short-lived token; this
stack creates the AWS side of that trust -- an OIDC identity provider
plus a role GitHub Actions can assume, scoped by the token's own `sub`
claim to *exactly* one thing, matched with `StringEquals` (not
`StringLike`).

That one thing is the GitHub *environment*, not the branch ref --
`repo:<org>/<repo>:environment:<name>`, not
`repo:<org>/<repo>:ref:refs/heads/main`. Tried the ref-based claim
first (the shape most OIDC-to-AWS guides lead with) and it failed live
with `Not authorized to perform sts:AssumeRoleWithWebIdentity` on the
first real deploy attempt: GitHub replaces the `sub` claim's shape
entirely once a job references `environment:` (as `ci.yml`'s `deploy`
job does, for its own required-reviewer approval gate).

**The environment-shaped claim still wasn't enough, and guessing a
second time was the actual mistake, not the first wrong guess itself**:
switching to `repo:<org>/<repo>:environment:<name>` (still using the
plain owner/repo *names*) failed the exact same way on the very next
real approval. Rather than guess a third shape, a temporary debug step
was added to `ci.yml` to decode and print the real token's claims
directly -- the actual `sub` GitHub issues includes each name's
*immutable numeric ID* inline:
`repo:<owner>@<owner_id>/<repo>@<repo_id>:environment:<name>` -- not
documented in the form most guides show, and not something a `StringLike`
prefix/suffix match would have papered over correctly either, since the
numeric IDs sit in the middle of the string. `github_owner_id` and
`github_repo_id` below are this repo's real values, confirmed
independently via `gh api repos/<org>/<repo> --jq '.id, .owner.id'`
against the live GitHub API, not just read once out of a token.

This ties two independent controls together usefully, not just
accidentally: the *only* way to reach this role is a job running under
the `production` environment specifically, which is the exact same
environment gated by a human's approval -- an attacker who somehow got
a workflow onto `main` still couldn't assume this role without also
clearing that same approval gate.

The role itself carries almost no direct permission of its own -- only
`sts:AssumeRole` on the CDK bootstrap's own roles (deploy,
file-publishing, image-publishing, lookup), the exact same roles
`cdk deploy` already uses for a human running it locally against this
account (already bootstrapped with the default `hnb659fds` qualifier --
confirmed via `cdk bootstrap`'s own CloudFormation stack, not assumed).
This stack doesn't widen what those roles can do; it only adds a second,
narrowly-scoped way to reach them, so the actual deploy permissions
continue to live exactly where they already lived.

**A third real failure, one layer past the OIDC fix**: even once
`sts:AssumeRoleWithWebIdentity` succeeded, `ci.yml`'s own
"write `frontend/.env.local`" step calls `aws cloudformation
describe-stacks` *directly* with this role's own credentials, not
through one of the bootstrap roles `cdk deploy` itself knows how to
assume -- `AccessDenied ... is not authorized to perform
cloudformation:DescribeStacks`, confirmed live, not assumed from
reading the workflow alone. `cloudformation:DescribeStacks` is granted
directly here, scoped to exactly the two stacks that step reads
(`CareAgentAuthStack`, `CareAgentApiStack`), not a broader
`cloudformation:*` or account-wide resource pattern -- the only
concession this stack makes to "almost no direct permission," and only
because a read-only describe call has no equivalent bootstrap-role path
the way the actual deploy does.
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
        github_owner: str = "tosspro23-cell",
        github_owner_id: str = "231253569",
        github_repo_name: str = "aws-healthcare-agent",
        github_repo_id: str = "1355988718",
        deploy_environment: str = "production",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        provider = iam.OpenIdConnectProvider(self, "GitHubOidcProvider", url=f"https://{_GITHUB_OIDC_HOST}", client_ids=[_STS_AUDIENCE])

        bootstrap_role_arns = [
            f"arn:aws:iam::{self.account}:role/cdk-{_CDK_QUALIFIER}-{role}-{self.account}-{self.region}"
            for role in ("deploy-role", "file-publishing-role", "image-publishing-role", "lookup-role")
        ]

        # The exact shape GitHub actually issues (confirmed by decoding a
        # real token, not assumed) -- see this class's own docstring for
        # the two prior, wrong guesses.
        sub_claim = f"repo:{github_owner}@{github_owner_id}/{github_repo_name}@{github_repo_id}:environment:{deploy_environment}"

        deploy_role = iam.Role(
            self,
            "GitHubActionsDeployRole",
            assumed_by=iam.WebIdentityPrincipal(
                provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        f"{_GITHUB_OIDC_HOST}:aud": _STS_AUDIENCE,
                        f"{_GITHUB_OIDC_HOST}:sub": sub_claim,
                    },
                },
            ),
            description=f"GitHub Actions OIDC -- only {github_owner}/{github_repo_name}'s {deploy_environment!r} env can assume this.",
        )
        # Not grant_read_write_data-style convenience: this is the exact
        # action needed (assume the bootstrap roles), on exactly the four
        # bootstrap role ARNs, nothing else -- the same scoping discipline
        # this project applies to every other IAM grant.
        deploy_role.add_to_policy(iam.PolicyStatement(actions=["sts:AssumeRole"], resources=bootstrap_role_arns))

        # ci.yml's own "write frontend/.env.local" step reads these two
        # stacks' outputs directly (no bootstrap-role equivalent for a
        # plain read) -- see this class's own docstring for the live
        # AccessDenied this closes. Read-only, and scoped to exactly the
        # two stacks that step actually queries, not every stack in the
        # account.
        describe_stacks_arns = [
            f"arn:aws:cloudformation:{self.region}:{self.account}:stack/{stack_name}/*"
            for stack_name in ("CareAgentAuthStack", "CareAgentApiStack")
        ]
        deploy_role.add_to_policy(iam.PolicyStatement(actions=["cloudformation:DescribeStacks"], resources=describe_stacks_arns))

        CfnOutput(self, "GitHubActionsDeployRoleArn", value=deploy_role.role_arn)
