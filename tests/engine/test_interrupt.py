"""Tests for the wind-down flag a termination signal raises.

The engine already degrades an over-running review to partial-results-with-a-
notice when ``max_review_seconds`` passes. An interruption (SIGTERM from a job
timeout or a cancelled workflow) sets the SAME state, so everything downstream
— skipped calls, the failed-call notice, the hidden incomplete marker, the
skipped reflection — is shared; only the reason wording differs.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
)
from lgtmaybe.engine import (
    INCOMPLETE_MARKER,
    LLMReviewEngine,
    ReviewIncompleteError,
    clear_interrupt,
    interrupt_requested,
    request_interrupt,
)
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
def _clean_flag() -> Iterator[None]:
    """The flag is process-global: never let one test leak into the next."""
    clear_interrupt()
    yield
    clear_interrupt()


class _InterruptingProvider(FakeProvider):
    """Raises the wind-down flag from inside its first completion."""

    def __init__(self, text: str = _FINDING_JSON) -> None:
        super().__init__()
        self._text = text

    def complete(self, messages, model, **opts):  # type: ignore[override]
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        request_interrupt()
        return ProviderResult(text=self._text, input_tokens=1, output_tokens=1)


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


class TestInterrupt:
    def test_flag_starts_clear_and_round_trips(self) -> None:
        assert interrupt_requested() is False
        request_interrupt()
        assert interrupt_requested() is True
        clear_interrupt()
        assert interrupt_requested() is False

    def test_queued_calls_are_skipped_and_partial_findings_still_post(self) -> None:
        """The in-flight call finishes and its findings survive; the queued rest
        are skipped, exactly as when the deadline passes."""
        provider = _InterruptingProvider()
        findings, summary = LLMReviewEngine(provider).review(_CTX, _cfg())
        assert len(provider.calls) == 1
        assert findings, "the completed call's findings still post"
        assert "review calls failed" in summary
        assert INCOMPLETE_MARKER in summary
        assert "LGTM" not in summary

    def test_notice_names_the_interruption_not_the_deadline(self) -> None:
        """Same machinery, distinguishable cause: a user reading the notice must
        not be sent hunting for a `max_review_seconds` they never hit."""
        _, summary = LLMReviewEngine(_InterruptingProvider()).review(_CTX, _cfg())
        assert "interrupted" in summary
        assert "max_review_seconds" not in summary

    def test_reflection_is_skipped_with_an_honest_notice(self) -> None:
        cfg = _cfg(categories=[ReviewCategory.security], reflect=True)
        provider = _InterruptingProvider()
        findings, summary = LLMReviewEngine(provider).review(_CTX, cfg)
        # One review call, then reflection would start after the interruption.
        assert len(provider.calls) == 1
        assert findings, "kept unaudited rather than dropped"
        assert "self-reflection audit was skipped" in summary
        assert "interrupted" in summary.lower()

    def test_every_call_skipped_still_fails_loud(self) -> None:
        """An interruption must never turn a total failure into a silent LGTM."""
        with pytest.raises(ReviewIncompleteError):
            LLMReviewEngine(_InterruptingProvider(text="no json")).review(_CTX, _cfg())
