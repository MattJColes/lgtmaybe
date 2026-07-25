from __future__ import annotations

import os
import sys
from pathlib import Path

from aws_cdk import App, Aspects, Environment
from cdk_nag import AwsSolutionsChecks


def main() -> None:
    sys.path.insert(0, str(Path(__file__).parents[2]))
    from infra.identity.stack import IdentityBrokerStack

    app = App()
    stack = IdentityBrokerStack(
        app,
        "LgtmaybeGithubAppIdentity",
        app_id=app.node.try_get_context("githubAppId") or "3987976",
        alarm_email=app.node.try_get_context("alarmEmail") or "matt@coles.codes",
        env=Environment(
            account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
            region=os.environ.get("CDK_DEFAULT_REGION", "ap-southeast-2"),
        ),
    )
    Aspects.of(stack).add(AwsSolutionsChecks(verbose=True))
    app.synth()


if __name__ == "__main__":
    main()
