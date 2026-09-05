"""AuthStack: Cognito User Pool + Hosted UI domain + App Client.

Deliberately its own stack, separate from ApiStack/DataStack, so it can be
deployed and reasoned about independently -- Phase 1's skeleton didn't need
to touch this at all, and Phase 2 only adds this stack plus wiring a JWT
authorizer onto the existing `/ask` route (see `api_stack.py`); no change
to the Lambda handler itself, since auth is enforced at the API Gateway
layer before the handler ever runs.

The App Client is a *public* client (no client secret) configured for the
Authorization Code + PKCE flow -- the right shape for a CLI/native client
that can't keep a secret confidential. `../scripts/get_dev_token.py` walks
that flow locally to get a token for testing.
"""

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from constructs import Construct

LOCAL_REDIRECT_URI = "http://localhost:8765/callback"
LOCAL_LOGOUT_URI = "http://localhost:8765/logout"


class AuthStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, domain_prefix: str | None = None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if domain_prefix is None:
            # Cognito Hosted UI domain prefixes are globally unique across
            # all AWS accounts (they live under
            # *.auth.<region>.amazoncognito.com), so the default needs
            # *some* account-specific component to avoid colliding with
            # every other deployment of this same open-source project.
            # `self.account` is a CDK token resolved to the real deploying
            # account at synth/deploy time -- not a hardcoded literal in
            # source, unlike the account ID this project's own account
            # used to have committed here directly. An independent review
            # flagged that as an unnecessary disclosure/portability issue
            # (not a credential leak -- an account ID alone grants no
            # access -- but anyone forking this repo had to notice and
            # change it before they could deploy). See docs/DECISIONS.md.
            domain_prefix = f"care-agent-{self.account}"

        self.user_pool = cognito.UserPool(
            self,
            "UserPool",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=False,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.domain = self.user_pool.add_domain(
            "UserPoolDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
        )

        self.app_client = self.user_pool.add_client(
            "AppClient",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_srp=True),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
                callback_urls=[LOCAL_REDIRECT_URI],
                logout_urls=[LOCAL_LOGOUT_URI],
            ),
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
            prevent_user_existence_errors=True,
        )

        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        CfnOutput(self, "AppClientId", value=self.app_client.user_pool_client_id)
        CfnOutput(self, "HostedUiDomain", value=f"{domain_prefix}.auth.{self.region}.amazoncognito.com")
