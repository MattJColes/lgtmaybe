"""Tests for the soft whole-review token budget (max_review_tokens)."""

from __future__ import annotations

import pytest

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
)
from lgtmaybe.engine import LLMReviewEngine, ReviewIncompleteError
from lgtmaybe.engine.profiling import profiler
from tests.fakes import FakeProvider

_CTX = PRContext(
    diff="@@ -1,3 +1,4 @@\n context\n+new line\n context\n",
    changed_files=["a.py"],
    base_sha="abc",
    head_sha="def",
    repo="org/repo",
    pr_number=1,
)

_FINDING_JSON = (
    '[{"path": "a.py", "line": 1, "severity": "high", "title": "bug", "body": "broken",'
    ' "failure_scenario": "When the changed line runs, the operation fails."}]'
)


@pytest.fixture(autouse=True)
def _fresh_profiler() -> None:
    """The profiler is a module-level singleton; start every test from zero."""
    profiler.reset()


class _CostlyProvider(FakeProvider):
    """Each completion reports a fixed token cost and returns one finding."""

    def __init__(self, input_tokens: int = 600, output_tokens: int = 400) -> None:
        super().__init__()
        self._input = input_tokens
        self._output = output_tokens

    def complete(self, messages, model, **opts):  # type: ignore[override]
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        return ProviderResult(
            text=_FINDING_JSON, input_tokens=self._input, output_tokens=self._output
        )


def _cfg(**overrides: object) -> ReviewConfig:
    defaults: dict[str, object] = {
        "provider": Provider.openai,
        # Serial on purpose, so the ceiling tripping during the first call leaves
        # the rest queued and skippable. Stated outright rather than leaning on a
        # provider that used to resolve to one worker — local providers now get
        # the same six as cloud, so the proxy no longer holds. While it did, this
        # suite failed roughly one run in ten: the ceiling still tripped, just one
        # call later. A ceiling test that passes by luck is worse than one that
        # fails.
        "max_concurrency": 1,
        "model": "m",
        "categories": [
            ReviewCategory.security,
            ReviewCategory.correctness,
            ReviewCategory.performance,
        ],
        "reflect": False,
    }
    defaults.update(overrides)
    return ReviewConfig(**defaults)  # type: ignore[arg-type]


class TestReviewTokenBudget:
    def test_calls_past_the_budget_are_skipped_with_a_notice(self) -> None:
        """The first call spends the whole 1,000-token budget; the queued rest
        must be skipped, the completed call's findings must still post, and the
        summary must name the knob that stopped the run."""
        provider = _CostlyProvider(input_tokens=600, output_tokens=400)
        findings, summary = LLMReviewEngine(provider).review(_CTX, _cfg(max_review_tokens=1000))
        assert len(provider.calls) == 1, "only the call that fit the budget ran"
        assert findings, "the completed call's findings still post"
        assert "max_review_tokens" in summary
        assert "LGTM" not in summary

    def test_budget_zero_disables_the_ceiling(self) -> None:
        provider = _CostlyProvider()
        _, summary = LLMReviewEngine(provider).review(_CTX, _cfg(max_review_tokens=0))
        assert len(provider.calls) == 3
        assert "max_review_tokens" not in summary

    def test_off_by_default(self) -> None:
        """Enforcement is opt-in: token spend varies far too much by repo for a
        default cap to be anything but a silent quality regression."""
        assert _cfg().max_review_tokens == 0
        provider = _CostlyProvider()
        _, summary = LLMReviewEngine(provider).review(_CTX, _cfg())
        assert len(provider.calls) == 3
        assert "max_review_tokens" not in summary

    def test_a_generous_budget_never_trips_a_normal_run(self) -> None:
        provider = _CostlyProvider()
        _, summary = LLMReviewEngine(provider).review(_CTX, _cfg(max_review_tokens=1_000_000))
        assert len(provider.calls) == 3
        assert "max_review_tokens" not in summary

    def test_budget_is_scoped_to_this_review_not_the_process(self) -> None:
        """Spend from an earlier review in the same process must not eat this
        one's budget — the ceiling is measured from where this run started."""
        engine = LLMReviewEngine(_CostlyProvider())
        engine.review(_CTX, _cfg(max_review_tokens=0))  # burns 3,000 tokens
        provider = _CostlyProvider()
        _, summary = LLMReviewEngine(provider).review(_CTX, _cfg(max_review_tokens=1_000_000))
        assert len(provider.calls) == 3
        assert "max_review_tokens" not in summary

    def test_every_call_skipped_still_fails_loud(self) -> None:
        """A budget must never turn a total failure into a silent LGTM."""

        class _CostlyUnparseable(FakeProvider):
            def complete(self, messages, model, **opts):  # type: ignore[override]
                self.calls.append({"messages": messages, "model": model, "opts": opts})
                return ProviderResult(text="no json", input_tokens=600, output_tokens=400)

        with pytest.raises(ReviewIncompleteError):
            LLMReviewEngine(_CostlyUnparseable()).review(_CTX, _cfg(max_review_tokens=1000))

    def test_reflection_runs_even_past_the_budget(self) -> None:
        """The token-budget equivalent of the deadline case. Passing the token
        ceiling is not a reason to post unaudited findings: the lens fan-out
        already stops short of it to leave the auditor its share."""
        provider = _CostlyProvider()
        cfg = _cfg(
            categories=[ReviewCategory.security],
            reflect=True,
            max_review_tokens=1000,
        )
        findings, summary = LLMReviewEngine(provider).review(_CTX, cfg)
        assert len(provider.calls) == 2, "the second call is the audit"
        assert findings
        assert "self-reflection audit was skipped" not in summary
