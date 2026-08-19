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
    '[{"path": "a.py", "line": 1, "severity": "high", "title": "bug", "body": "broken",'
    ' "failure_scenario": "When the changed line runs, the operation fails."}]'
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
        "provider": Provider.openai,
        # Serial on purpose, so a deadline crossed during the first call leaves
        # the rest queued and skippable. Stated outright rather than leaning on a
        # provider that used to resolve to one worker — local providers now get
        # the same six as cloud, so the proxy no longer holds.
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

    def test_reflection_still_runs_when_lens_calls_exhaust_the_deadline(self) -> None:
        """An overrun must not skip the audit.

        Benchmark evidence: a runaway lens call consumed the whole time budget,
        which skipped reflection, and 325 unaudited findings posted, 323 of them
        false positives on a diff with nothing wrong in it. Lens calls now stop
        early enough to leave the auditor room.
        """
        provider = _SlowProvider(delay=1.2)
        cfg = _cfg(max_review_seconds=1, reflect=True)
        _, summary = LLMReviewEngine(provider).review(_CTX, cfg)
        reflect_calls = [
            call
            for call in provider.calls
            if "false positive" in str(call["messages"]).lower()
            or "confidence" in str(call["messages"]).lower()
        ]
        assert reflect_calls, "reflection must still run after the lens deadline"
        assert "skipping reflection" not in summary

    def test_deadline_zero_disables_the_ceiling(self) -> None:
        provider = _SlowProvider(delay=0.0)
        _, summary = LLMReviewEngine(provider).review(_CTX, _cfg(max_review_seconds=0))
        assert len(provider.calls) == 3
        assert "deadline" not in summary

    def test_generous_default_never_trips_a_normal_run(self) -> None:
        provider = _SlowProvider(delay=0.0)
        cfg = _cfg()
        # 2× the generous per-call timeout (1800s), so one slow gateway/local
        # call can't eat the whole review budget on its own.
        assert cfg.max_review_seconds == 3600
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

    def test_reflection_runs_even_past_the_deadline(self) -> None:
        """Passing the ceiling must not skip the audit.

        Reflection used to be gated on the same clock the lens fan-out had just
        passed, so a runaway removed the stage that prunes its output. Reflection
        is one bounded call, cheaper than posting the false positives it drops.
        """
        provider = _SlowProvider(delay=1.2)
        cfg = _cfg(
            categories=[ReviewCategory.security],
            reflect=True,
            max_review_seconds=1,
        )
        findings, summary = LLMReviewEngine(provider).review(_CTX, cfg)
        # One review call (slow), then reflection despite the blown ceiling.
        assert len(provider.calls) == 2, "the second call is the audit"
        assert findings
        assert "self-reflection audit was skipped" not in summary
