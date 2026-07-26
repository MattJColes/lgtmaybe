"""Floors for every wall-clock budget lgtmaybe enforces.

A timeout only earns its keep by capping a *pathological* run. Set one tight
enough to trip a healthy-but-slow one and it stops being a safety net: the
review posts "⚠️ N of M review calls failed … results may be incomplete" with
zero findings, which reads to a human exactly like a clean bill of health.

Most are deliberately floors, not equalities — raising a budget is always
safe there, lowering one below the floor is the regression worth catching. The
per-provider model-call defaults are the exception: they are what the docs
*promise*, so they are pinned to the documented values exactly (see
:class:`TestDocumentedProviderDefaults`).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from lgtmaybe.core.models import Provider, ReviewConfig
from lgtmaybe.providers.factory import default_timeout_for

_REPO_ROOT = Path(__file__).parent.parent
_ACTION_YML = _REPO_ROOT / "action.yml"
_ACTION_GUIDE = _REPO_ROOT / "docs" / "how-to" / "use-as-github-action.md"

# The providers documented as getting the generous default: ones that may front a
# slow model (a local server, or openrouter's gateway to arbitrary models).
_SLOW_PROVIDERS = frozenset(
    {Provider.ollama, Provider.openai_compatible, Provider.openrouter},
)


def _states_seconds(text: str, seconds: int) -> bool:
    """Whether *text* quotes *seconds* as a whole number.

    Substring matching would pass ``60`` against a documented ``600`` — exactly
    the drift this guard exists to catch — so the digits must not sit inside a
    longer number.
    """
    return re.search(rf"(?<!\d){seconds}(?!\d)", text) is not None


def _documents_the_split(text: str, *, slow_seconds: int, cloud_seconds: int) -> bool:
    """Whether *text* attaches each budget to the providers it applies to.

    Checking the names and the numbers independently would pass a description that
    swapped them ("openrouter 600, cloud 1800") — the very drift being guarded — so
    the slow budget must appear in the span that names the slow providers, and the
    cloud budget in the span introduced by "cloud". Both documented forms run
    "<slow providers> <slow>s, cloud <cloud>s".
    """
    match = re.search(r"(?P<slow>ollama.*?)(?P<cloud>cloud\D*\d+)", text, re.IGNORECASE)
    if match is None:
        return False
    return _states_seconds(match["slow"], slow_seconds) and _states_seconds(
        match["cloud"], cloud_seconds
    )


class TestModelCallBudgets:
    def test_whole_review_ceiling_holds_two_slow_calls(self) -> None:
        """The soft whole-review deadline must stay at least 2× the most generous
        per-call budget, so one slow gateway/local call can never eat the run."""
        slowest = max(default_timeout_for(p) for p in Provider)
        assert ReviewConfig(provider=Provider.openai, model="m").max_review_seconds >= 2 * slowest


class TestDocumentedProviderDefaults:
    """The resolved per-call timeout must equal what the docs promise, provider
    by provider.

    A floor can't catch this class of drift: reclassifying a provider (openrouter
    once counted as fast cloud, on a 60s budget, while the docs advertised the
    generous default) leaves every floor green and every promise broken. The user
    has no way to see which budget they actually got — the failure looks like a
    clean review — so the numbers in the prose and the numbers in the code are
    pinned to each other here.
    """

    # The documented split: generous for the slow-capable providers, short for
    # direct cloud. Written out (not derived from the code under test) so a change
    # to either side has to be made deliberately, in both places.
    SLOW_SECONDS = 1800
    CLOUD_SECONDS = 600

    def test_every_provider_resolves_its_documented_timeout(self) -> None:
        expected = {
            p: self.SLOW_SECONDS if p in _SLOW_PROVIDERS else self.CLOUD_SECONDS for p in Provider
        }
        assert {p: default_timeout_for(p) for p in Provider} == expected

    def test_action_yml_documents_the_resolved_timeouts(self) -> None:
        """The ``timeout`` input's description must name the slow providers and
        quote the budgets the code actually resolves."""
        inputs = yaml.safe_load(_ACTION_YML.read_text(encoding="utf-8"))["inputs"]
        description = inputs["timeout"]["description"]

        for provider in sorted(p.value for p in _SLOW_PROVIDERS):
            assert provider in description, f"action.yml must name {provider} as slow-capable"
        assert _documents_the_split(
            description,
            slow_seconds=default_timeout_for(Provider.openrouter),
            cloud_seconds=default_timeout_for(Provider.openai),
        )

    def test_action_guide_documents_the_resolved_timeouts(self) -> None:
        """The Action how-to's input table is the other place a user reads these
        numbers, so it is held to the same equality."""
        row = next(
            line
            for line in _ACTION_GUIDE.read_text(encoding="utf-8").splitlines()
            if line.startswith("| `timeout` |")
        )
        for provider in sorted(p.value for p in _SLOW_PROVIDERS):
            assert provider in row, f"the input table must name {provider} as slow-capable"
        assert _documents_the_split(
            row,
            slow_seconds=default_timeout_for(Provider.openrouter),
            cloud_seconds=default_timeout_for(Provider.openai),
        )


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
