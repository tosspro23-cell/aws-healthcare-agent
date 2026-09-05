"""Assertions against BudgetStack's synthesized CloudFormation -- the
account-wide cost alert (see `../stacks/budget_stack.py` for why it's
opt-in rather than always deployed).
"""

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template
from stacks.budget_stack import BudgetStack

_TEST_EMAIL = "test-alerts@example.com"


def _synth_budget_stack():
    app = cdk.App()
    stack = BudgetStack(app, "TestBudgetStack", notification_email=_TEST_EMAIL)
    return Template.from_stack(stack)


def test_budget_has_a_positive_monthly_cost_limit():
    template = _synth_budget_stack()
    template.has_resource_properties(
        "AWS::Budgets::Budget",
        {
            "Budget": Match.object_like(
                {"BudgetType": "COST", "TimeUnit": "MONTHLY", "BudgetLimit": {"Unit": "USD", "Amount": Match.any_value()}}
            )
        },
    )
    budgets = template.find_resources("AWS::Budgets::Budget")
    (budget,) = budgets.values()
    assert budget["Properties"]["Budget"]["BudgetLimit"]["Amount"] > 0


def test_budget_notifies_the_configured_email_not_a_placeholder():
    template = _synth_budget_stack()
    budgets = template.find_resources("AWS::Budgets::Budget")
    (budget,) = budgets.values()
    notifications = budget["Properties"]["NotificationsWithSubscribers"]
    assert len(notifications) >= 1
    for n in notifications:
        addresses = [s["Address"] for s in n["Subscribers"]]
        assert addresses == [_TEST_EMAIL]
        for s in n["Subscribers"]:
            assert s["SubscriptionType"] == "EMAIL"


def test_budget_has_both_an_actual_and_a_forecasted_notification():
    """A single "you already went over" alert is a lagging indicator --
    a forecasted notification catches the trend before the month closes."""
    template = _synth_budget_stack()
    budgets = template.find_resources("AWS::Budgets::Budget")
    (budget,) = budgets.values()
    types = {n["Notification"]["NotificationType"] for n in budget["Properties"]["NotificationsWithSubscribers"]}
    assert "ACTUAL" in types
    assert "FORECASTED" in types


def test_only_one_budget_resource_exists():
    template = _synth_budget_stack()
    template.resource_count_is("AWS::Budgets::Budget", 1)
