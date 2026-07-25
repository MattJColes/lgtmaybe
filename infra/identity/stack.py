from __future__ import annotations

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
)
from aws_cdk import (
    aws_apigatewayv2 as apigwv2,
)
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
)
from aws_cdk import (
    aws_cloudwatch_actions as cloudwatch_actions,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_kms as kms,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)
from aws_cdk import (
    aws_sns as sns,
)
from aws_cdk import (
    aws_sns_subscriptions as subscriptions,
)
from aws_cdk.aws_apigatewayv2_authorizers import HttpJwtAuthorizer
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from aws_cdk.aws_lambda_python_alpha import BundlingOptions, PythonFunction
from cdk_nag import NagSuppressions
from constructs import Construct
from services.github_app_identity.broker import OIDC_AUDIENCE, OIDC_ISSUER

SERVICE_ROOT = Path(__file__).parents[2] / "services" / "github_app_identity"


class IdentityBrokerStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        app_id: str,
        alarm_email: str,
        bundle_code: bool = True,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        private_key = secretsmanager.Secret(
            self,
            "AppPrivateKey",
            secret_name="lgtmaybe/github-app/private-key",
            description="Private key for the public lgtmaybe GitHub App",
            removal_policy=RemovalPolicy.RETAIN,
        )
        NagSuppressions.add_resource_suppressions(
            private_key,
            [
                {
                    "id": "AwsSolutions-SMG4",
                    "reason": (
                        "GitHub App private keys are rotated in GitHub and cannot use "
                        "Secrets Manager's database rotation contract."
                    ),
                }
            ],
        )
        log_group = logs.LogGroup(
            self,
            "BrokerLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        broker = self._function(
            app_id=app_id,
            secret_arn=private_key.secret_arn,
            log_group=log_group,
            bundle_code=bundle_code,
        )
        private_key.grant_read(broker)

        api = apigwv2.HttpApi(
            self,
            "IdentityApi",
            api_name="lgtmaybe-github-app-identity",
            description="Exchanges GitHub Actions OIDC for a scoped lgtmaybe App token",
            create_default_stage=False,
        )
        authorizer = HttpJwtAuthorizer(
            "GitHubOidc",
            OIDC_ISSUER,
            jwt_audience=[OIDC_AUDIENCE],
            identity_source=["$request.header.Authorization"],
        )
        api.add_routes(
            path="/token",
            methods=[apigwv2.HttpMethod.POST],
            integration=HttpLambdaIntegration("BrokerIntegration", broker),
            authorizer=authorizer,
        )
        api_log_group = logs.LogGroup(
            self,
            "ApiAccessLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        stage = apigwv2.HttpStage(
            self,
            "DefaultStage",
            http_api=api,
            stage_name="$default",
            auto_deploy=True,
            detailed_metrics_enabled=False,
            throttle=apigwv2.ThrottleSettings(rate_limit=2, burst_limit=5),
        )
        cfn_stage = stage.node.default_child
        assert isinstance(cfn_stage, apigwv2.CfnStage)
        cfn_stage.add_property_override(
            "AccessLogSettings",
            {
                "DestinationArn": api_log_group.log_group_arn,
                "Format": (
                    '{"requestId":"$context.requestId","routeKey":"$context.routeKey",'
                    '"status":"$context.status","responseLength":"$context.responseLength"}'
                ),
            },
        )
        NagSuppressions.add_resource_suppressions(
            stage,
            [
                {
                    "id": "AwsSolutions-APIG1",
                    "reason": (
                        "Access logging is applied directly to the underlying CfnStage "
                        "because this CDK HttpStage L2 has no concrete access-log settings type."
                    ),
                }
            ],
        )

        alarm_topic = sns.Topic(
            self,
            "AlarmTopic",
            display_name="lgtmaybe GitHub App identity alarms",
            enforce_ssl=True,
            master_key=kms.Alias.from_alias_name(self, "AlarmTopicKey", "alias/aws/sns"),
        )
        alarm_topic.add_subscription(subscriptions.EmailSubscription(alarm_email))
        broker_errors = cloudwatch.Alarm(
            self,
            "BrokerErrors",
            metric=broker.metric_errors(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        api_server_errors = cloudwatch.Alarm(
            self,
            "ApiServerErrors",
            metric=cloudwatch.Metric(
                namespace="AWS/ApiGateway",
                metric_name="5xx",
                dimensions_map={"ApiId": api.api_id, "Stage": stage.stage_name},
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        for alarm in (broker_errors, api_server_errors):
            alarm.add_alarm_action(cloudwatch_actions.SnsAction(alarm_topic))

        Tags.of(self).add("context", "github-app-identity")
        CfnOutput(self, "BrokerUrl", value=f"{api.api_endpoint}/token")
        CfnOutput(self, "PrivateKeySecretArn", value=private_key.secret_arn)

    def _function(
        self,
        *,
        app_id: str,
        secret_arn: str,
        log_group: logs.ILogGroup,
        bundle_code: bool,
    ) -> lambda_.Function:
        role = iam.Role(
            self,
            "BrokerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        log_group.grant_write(role)
        common: dict[str, object] = {
            "runtime": lambda_.Runtime.PYTHON_3_14,
            "architecture": lambda_.Architecture.X86_64,
            "handler": "handler" if bundle_code else "handler.handler",
            "timeout": Duration.seconds(15),
            "memory_size": 256,
            "reserved_concurrent_executions": 5,
            "environment": {
                "GITHUB_APP_ID": app_id,
                "APP_PRIVATE_KEY_SECRET_ARN": secret_arn,
                "POWERTOOLS_SERVICE_NAME": "github-app-identity",
                "POWERTOOLS_LOG_LEVEL": "INFO",
            },
            "log_group": log_group,
            "logging_format": lambda_.LoggingFormat.JSON,
            "application_log_level_v2": lambda_.ApplicationLogLevel.INFO,
            "system_log_level_v2": lambda_.SystemLogLevel.WARN,
            "role": role,
        }
        if bundle_code:
            return PythonFunction(
                self,
                "Broker",
                entry=str(SERVICE_ROOT),
                index="handler.py",
                bundling=BundlingOptions(
                    asset_excludes=["__pycache__", "*.pyc", ".pytest_cache", ".venv"]
                ),
                **common,
            )
        return lambda_.Function(
            self,
            "Broker",
            code=lambda_.Code.from_asset(str(SERVICE_ROOT)),
            **common,
        )
