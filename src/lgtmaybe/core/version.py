"""The running lgtmaybe version.

Lives in ``core`` because both ends of the pipeline need it and neither may
import the other: the engine stamps it into the summary line, and the CLI logs
it and puts it on the failure notice.
"""

from __future__ import annotations

from importlib import metadata


def package_version() -> str:
    """The installed lgtmaybe version, or ``"unknown"`` when it can't be read.

    Reading it is best-effort by design: a source checkout that was never
    installed has no distribution metadata, and a missing version must never be
    the reason a review fails.
    """
    try:
        return metadata.version("lgtmaybe")
    except Exception:
        return "unknown"
