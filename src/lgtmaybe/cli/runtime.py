"""Per-invocation runtime options.

A small typed bag for the values that vary per call but aren't part of the
persisted ``ReviewConfig``: credentials supplied at call time, an optional
fallback model, and (for the GitHub paths) the PR URL. Frozen so it can't be
mutated in place — use ``dataclasses.replace`` to derive a copy with the URL
filled in.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeOptions:
    """Call-time options resolved from CLI flags or GitHub Action inputs."""

    api_key: str | None = None
    api_base: str | None = None
    fallback_model: str | None = None
    pr_url: str | None = None
    # Print the timing profile (per-stage + per-call tables, cache totals) at
    # the end of the run. Presentation only — it never changes review behaviour
    # — which is why it lives here and not on ReviewConfig.
    profile: bool = False
