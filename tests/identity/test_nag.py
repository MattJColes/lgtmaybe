from __future__ import annotations

import warnings
from pathlib import Path

with warnings.catch_warnings():
    warnings.simplefilter("ignore", EncodingWarning)
    from infra.identity.app import build_app


def test_synth_reports_no_unacknowledged_aws_solutions_findings(tmp_path: Path) -> None:
    """The deployed app must pass the AwsSolutions pack.

    cdk-nag v3 runs as a CDK validation plugin: any finding that is not
    acknowledged interrupts ``synth()``. Synthesising here is therefore the
    whole assertion -- it proves both that the pack is still wired up and that
    the stack's acknowledgements still match the rules it trips.
    """
    build_app(bundle_code=False, outdir=str(tmp_path)).synth()
