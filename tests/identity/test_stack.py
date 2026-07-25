from __future__ import annotations

import warnings
from pathlib import Path

with warnings.catch_warnings():
    warnings.simplefilter("ignore", EncodingWarning)
    import infra.identity.stack as identity_stack
    from aws_cdk import App
    from aws_cdk import aws_lambda as lambda_
    from aws_cdk.assertions import Match, Template
    from infra.identity.stack import IdentityBrokerStack

from services.github_app_identity.broker import OIDC_AUDIENCE, OIDC_ISSUER


def _template() -> Template:
    stack = IdentityBrokerStack(
        App(),
        "IdentityBroker",
        app_id="3987976",
        alarm_email="matt@coles.codes",
        bundle_code=False,
    )
    return Template.from_stack(stack)


def test_stack_provisions_a_bounded_python_lambda_and_retained_secret() -> None:
    template = _template()

    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Runtime": "python3.14",
            "Handler": "handler.handler",
            "Architectures": ["x86_64"],
            "ReservedConcurrentExecutions": 5,
            "Timeout": 15,
            "Environment": {
                "Variables": {
                    "GITHUB_APP_ID": "3987976",
                    "APP_PRIVATE_KEY_SECRET_ARN": Match.any_value(),
                }
            },
        },
    )
    template.has_resource(
        "AWS::SecretsManager::Secret",
        {
            "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain",
        },
    )


def test_bundled_stack_names_the_exported_handler_once(monkeypatch) -> None:
    python_function_arguments: dict[str, object] = {}

    def fake_python_function(
        scope,
        construct_id: str,
        *,
        entry: str,
        index: str,
        bundling,
        handler: str,
        **kwargs,
    ):
        python_function_arguments.update({"entry": entry, "index": index, "handler": handler})
        return lambda_.Function(
            scope,
            construct_id,
            code=lambda_.Code.from_asset(entry),
            handler=f"{Path(index).stem}.{handler}",
            **kwargs,
        )

    monkeypatch.setattr(identity_stack, "PythonFunction", fake_python_function)

    stack = IdentityBrokerStack(
        App(),
        "BundledIdentityBroker",
        app_id="3987976",
        alarm_email="matt@coles.codes",
    )
    template = Template.from_stack(stack)

    assert python_function_arguments["index"] == "handler.py"
    assert python_function_arguments["handler"] == "handler"
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Handler": "handler.handler"},
    )


def test_stack_restricts_the_secret_grant_to_get_secret_value() -> None:
    policies = _template().find_resources("AWS::IAM::Policy")
    statements = [
        statement
        for policy in policies.values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
    ]
    secret_grants = [
        statement
        for statement in statements
        if "secretsmanager:GetSecretValue"
        in (statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]])
    ]

    assert len(secret_grants) == 1
    assert set(secret_grants[0]["Action"]) == {
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret",
    }
    assert secret_grants[0]["Resource"] != "*"


def test_stack_puts_github_oidc_authorization_and_throttling_in_front() -> None:
    template = _template()

    template.has_resource_properties(
        "AWS::ApiGatewayV2::Authorizer",
        {
            "AuthorizerType": "JWT",
            "IdentitySource": ["$request.header.Authorization"],
            "JwtConfiguration": {
                "Audience": [OIDC_AUDIENCE],
                "Issuer": OIDC_ISSUER,
            },
        },
    )
    template.has_resource_properties(
        "AWS::ApiGatewayV2::Route",
        {
            "AuthorizationType": "JWT",
            "RouteKey": "POST /token",
        },
    )
    template.has_resource_properties(
        "AWS::ApiGatewayV2::Stage",
        {
            "AutoDeploy": True,
            "DefaultRouteSettings": {
                "DetailedMetricsEnabled": False,
                "ThrottlingBurstLimit": 5,
                "ThrottlingRateLimit": 2,
            },
            "AccessLogSettings": {
                "DestinationArn": Match.any_value(),
                "Format": Match.any_value(),
            },
        },
    )


def test_stack_uses_a_resource_scoped_lambda_role() -> None:
    roles = _template().find_resources("AWS::IAM::Role")

    assert all("ManagedPolicyArns" not in role["Properties"] for role in roles.values())


def test_stack_alarms_on_lambda_errors_and_api_throttles() -> None:
    template = _template()
    alarms = template.find_resources("AWS::CloudWatch::Alarm")

    assert len(alarms) == 2
    alarm_actions = [alarm["Properties"]["AlarmActions"] for alarm in alarms.values()]
    assert all(len(actions) == 1 for actions in alarm_actions)
    assert alarm_actions[0] == alarm_actions[1]


def test_stack_sends_alarm_notifications_to_the_maintainer() -> None:
    template = _template()

    template.resource_count_is("AWS::SNS::Topic", 1)
    template.has_resource_properties(
        "AWS::SNS::Subscription",
        {
            "Endpoint": "matt@coles.codes",
            "Protocol": "email",
            "TopicArn": Match.any_value(),
        },
    )
