"""Floors for every wall-clock budget lgtmaybe enforces.

A timeout only earns its keep by capping a *pathological* run. Set one tight
enough to trip a healthy-but-slow one and it stops being a safety net: the
review posts "⚠️ N of M review calls failed … results may be incomplete" with
zero findings, which reads to a human exactly like a clean bill of health.

These are deliberately floors, not equalities — raising a budget is always
safe here, lowering one below the floor is the regression worth catching.
"""

from __future__ import annotations

from lgtmaybe.core.models import Provider, ReviewConfig
from lgtmaybe.providers.factory import default_timeout_for


class TestModelCallBudgets:
    def test_whole_review_ceiling_holds_two_slow_calls(self) -> None:
        """The soft whole-review deadline must stay at least 2× the most generous
        per-call budget, so one slow gateway/local call can never eat the run."""
        slowest = max(default_timeout_for(p) for p in Provider)
        assert ReviewConfig(provider=Provider.openai, model="m").max_review_seconds >= 2 * slowest


class TestSupportingBudgets:
    """The non-model timeouts around the review: GitHub I/O, git, and the
    sandboxed analysis subprocesses. A cold CI runner is slower than a laptop."""

    def test_github_api_calls_get_a_minute(self) -> None:
        from lgtmaybe.github.rest_gateway import _TIMEOUT

        assert _TIMEOUT.connect is not None and _TIMEOUT.read is not None
        assert min(_TIMEOUT.connect, _TIMEOUT.read) >= 60

    def test_local_git_commands_get_two_minutes(self) -> None:
        from lgtmaybe.local import _TIMEOUT

        assert _TIMEOUT >= 120

    def test_base_branch_clone_gets_five_minutes(self) -> None:
        from lgtmaybe.github.checkout import _CLONE_TIMEOUT

        assert _CLONE_TIMEOUT >= 300

    def test_static_analysis_tools_get_three_minutes(self) -> None:
        from lgtmaybe.engine.static_analysis import _TOOL_TIMEOUT

        assert _TOOL_TIMEOUT >= 180

    def test_astgrep_scan_gets_a_minute(self) -> None:
        from lgtmaybe.engine.astgrep import _SCAN_TIMEOUT

        assert _SCAN_TIMEOUT >= 60
