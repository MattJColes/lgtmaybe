"""Tests for the soft whole-review deadline (max_review_seconds)."""

from __future__ import annotations

import time

import pytest

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
)
from lgtmaybe.engine import LLMReviewEngine, ReviewIncompleteError
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
    '[{"path": "a.py", "line": 1, "severity": "high", "title": "bug", "body": "broken"}]'
)


class _SlowProvider(FakeProvider):
    """Each completion takes ``delay`` seconds and returns one finding."""

    def __init__(self, delay: float) -> None:
        super().__init__()
        self._delay = delay

    def complete(self, messages, model, **opts):  # type: ignore[override]
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        time.sleep(self._delay)
        return ProviderResult(text=_FINDING_JSON, input_tokens=1, output_tokens=1)


def _cfg(**overrides: object) -> ReviewConfig:
    defaults: dict[str, object] = {
        "provider": Provider.ollama,  # serial: calls execute one at a time
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


class TestReviewDeadline:
    def test_calls_past_the_deadline_are_skipped_with_a_notice(self) -> None:
        """First call overruns the 1s ceiling; the queued rest must be skipped
        (in-flight finishes, its findings post) and the summary must say so."""
        provider = _SlowProvider(delay=1.2)
        findings, summary = LLMReviewEngine(provider).review(_CTX, _cfg(max_review_seconds=1))
        assert len(provider.calls) == 1  # only the in-flight call ran
        assert findings, "the completed call's findings still post"
        assert "review calls failed" in summary and "deadline" in summary
        assert "LGTM" not in summary

    def test_deadline_zero_disables_the_ceiling(self) -> None:
        provider = _SlowProvider(delay=0.0)
        _, summary = LLMReviewEngine(provider).review(_CTX, _cfg(max_review_seconds=0))
        assert len(provider.calls) == 3
        assert "deadline" not in summary

    def test_generous_default_never_trips_a_normal_run(self) -> None:
        provider = _SlowProvider(delay=0.0)
        cfg = _cfg()
        assert cfg.max_review_seconds == 600
        _, summary = LLMReviewEngine(provider).review(_CTX, cfg)
        assert len(provider.calls) == 3
        assert "deadline" not in summary

    def test_every_call_skipped_still_fails_loud(self) -> None:
        """A deadline must never turn a total failure into a silent LGTM: when
        the only call that ran produced nothing usable and the rest were
        skipped, the ReviewIncompleteError path stays."""

        class _SlowUnparseable(FakeProvider):
            def complete(self, messages, model, **opts):  # type: ignore[override]
                self.calls.append({"messages": messages, "model": model, "opts": opts})
                time.sleep(1.2)
                return ProviderResult(text="no json", input_tokens=1, output_tokens=1)

        with pytest.raises(ReviewIncompleteError):
            LLMReviewEngine(_SlowUnparseable()).review(_CTX, _cfg(max_review_seconds=1))

    def test_reflection_skipped_past_deadline_with_an_honest_notice(self) -> None:
        provider = _SlowProvider(delay=1.2)
        cfg = _cfg(
            categories=[ReviewCategory.security],
            reflect=True,
            max_review_seconds=1,
        )
        findings, summary = LLMReviewEngine(provider).review(_CTX, cfg)
        # One review call (slow), then reflection would start past the ceiling.
        assert len(provider.calls) == 1
        assert findings  # kept unaudited rather than dropped
        assert "self-reflection audit was skipped" in summary
