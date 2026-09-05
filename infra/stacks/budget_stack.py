"""BudgetStack: a monthly AWS Budget with email alerts -- the account-wide
counterpart to `ApiStack`'s per-request throttle. The throttle caps how
*fast* a leaked credential or a runaway client could spend; this catches
the slower case (a real forecast trending over budget) that a rate limit
alone wouldn't.

Only created when `CARE_AGENT_BUDGET_EMAIL` is set (see `app.py`) --
deliberately not hardcoded into committed source, since this repository
is public and an email address is more personal than the account IDs
this project already avoids hardcoding (see docs/DECISIONS.md's ADR on
`auth_stack.py`'s domain prefix). A budget with no meaningful subscriber
configured isn't worth deploying at all, so the stack itself is optional
rather than always-present-but-half-configured.

`AWS::Budgets::Budget` is a real resource, not a moto-mockable Lambda
handler -- covered here by `cdk synth` template assertions only (see
`tests/test_budget_stack.py`), the same rigor `test_stacks.py` already
applies to every other non-Lambda resource in this project.
"""

from aws_cdk import Stack
from aws_cdk import aws_budgets as budgets
from constructs import Construct

# A demo/portfolio project on synthetic data -- this is meant to catch
# "something is unexpectedly spending real money" (a leaked credential, a
# runaway retry loop), not to model actual expected usage.
_MONTHLY_LIMIT_USD = 10.0


class BudgetStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, notification_email: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        subscriber = budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL", address=notification_email)

        def notification(*, notification_type: str, threshold: float) -> budgets.CfnBudget.NotificationWithSubscribersProperty:
            return budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    notification_type=notification_type,
                    comparison_operator="GREATER_THAN",
                    threshold=threshold,
                    threshold_type="PERCENTAGE",
                ),
                subscribers=[subscriber],
            )

        budgets.CfnBudget(
            self,
            "MonthlyCostBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=_MONTHLY_LIMIT_USD, unit="USD"),
            ),
            notifications_with_subscribers=[
                # Actual spend already over 80% -- something happened, not
                # just a forecast.
                notification(notification_type="ACTUAL", threshold=80),
                # Forecasted to exceed the budget entirely by month's end --
                # earlier warning, based on the current spend trajectory.
                notification(notification_type="FORECASTED", threshold=100),
            ],
        )
