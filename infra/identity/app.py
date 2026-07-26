from __future__ import annotations

import os
import sys
from pathlib import Path

from aws_cdk import App, Environment, Validations
from cdk_nag import AwsSolutionsChecks


def build_app(*, bundle_code: bool = True, outdir: str | None = None) -> App:
    sys.path.insert(0, str(Path(__file__).parents[2]))
    from infra.identity.stack import IdentityBrokerStack

    app = App(outdir=outdir)
    IdentityBrokerStack(
        app,
        "LgtmaybeGithubAppIdentity",
        app_id=app.node.try_get_context("githubAppId") or "3987976",
        alarm_email=app.node.try_get_context("alarmEmail") or "matt@coles.codes",
        bundle_code=bundle_code,
        env=Environment(
            account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
            region=os.environ.get("CDK_DEFAULT_REGION", "ap-southeast-2"),
        ),
    )
    # cdk-nag v3 is a CDK validation plugin, not an aspect: registered on the
    # app, it interrupts synth on any finding the stack has not acknowledged.
    Validations.of(app).add_plugins(AwsSolutionsChecks(app, verbose=True))
    return app


def main() -> None:
    build_app().synth()


if __name__ == "__main__":
    main()
