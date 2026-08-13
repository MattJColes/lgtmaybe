"""Tests for the rescue wave: one more go at a call that failed transiently.

A single flaky provider call used to void a whole round's verdict — three
consecutive reviews each reported "1 of 4 review calls failed" while the other
three lenses succeeded, and the findings the failed lens would have made were
lost until somebody re-ran by hand. The rescue wave re-runs exactly those calls
once, after the main wave has drained, and costs nothing at all on a healthy run.
"""

from __future__ import annotations

import threading
from typing import Any

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
)
from lgtmaybe.core.ports import Message, ProviderTruncated, ProviderWallTimeout
from lgtmaybe.engine import LLMReviewEngine
from lgtmaybe.engine.engine import INCOMPLETE_MARKER
from tests.fakes import FakeProvider

_CTX = PRContext(
    diff=(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        "@@ -1,1 +1,3 @@\n context\n+new line\n+another line\n"
    ),
    changed_files=["a.py"],
    base_sha="abc",
    head_sha="def",
    repo="org/repo",
    pr_number=1,
)

_FINDING_JSON = (
    '[{"path": "a.py", "line": 2, "severity": "high", "title": "bug", "body": "broken",'
    ' "failure_scenario": "When the changed line runs, the operation fails."}]'
)

_SECURITY_MARKER = "Security review"


def _cfg(**overrides: Any) -> ReviewConfig:
    defaults: dict[str, Any] = {
        "provider": Provider.openai,
        "model": "m",
        "categories": [ReviewCategory.security, ReviewCategory.performance],
        "reflect": False,
    }
    defaults.update(overrides)
    return ReviewConfig(**defaults)


class _FlakyLens(FakeProvider):
    """Fails the security lens for its first ``failures`` calls, then answers."""

    def __init__(self, failures: int, exc: BaseException | None = None) -> None:
        super().__init__()
        self._remaining = failures
        self._exc = exc or RuntimeError("upstream hiccup")
        self._lock = threading.Lock()
        self.security_calls = 0

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        prompt = "\n".join(str(m.get("content", "")) for m in messages)
        with self._lock:
            self.calls.append({"messages": messages, "model": model, "opts": opts})
            if _SECURITY_MARKER in prompt:
                self.security_calls += 1
                if self._remaining > 0:
                    self._remaining -= 1
                    raise self._exc
        return ProviderResult(text=_FINDING_JSON, input_tokens=1, output_tokens=1)


class TestRescueWave:
    def test_a_lens_that_fails_once_is_re_run_and_the_round_completes(self) -> None:
        """The whole point: one transient failure no longer voids the round."""
        provider = _FlakyLens(failures=1)

        findings, summary = LLMReviewEngine(provider).review(_CTX, _cfg())

        assert provider.security_calls == 2  # the failure, then the rescue
        assert "review calls failed" not in summary
        assert INCOMPLETE_MARKER not in summary
        assert findings  # the rescued lens's findings are in the review

    def test_the_rescue_happens_once_not_in_a_loop(self) -> None:
        """Bounded to a single wave. A lens that is genuinely down gets one more
        go and then the round reports itself incomplete — it never grinds."""
        provider = _FlakyLens(failures=99)

        _, summary = LLMReviewEngine(provider).review(_CTX, _cfg())

        assert provider.security_calls == 2
        assert "1 of 2 review calls failed" in summary
        assert INCOMPLETE_MARKER in summary

    def test_a_healthy_run_costs_no_extra_calls(self) -> None:
        """Nothing failed, so there is nothing to rescue and nothing to pay for."""
        provider = _FlakyLens(failures=0)

        LLMReviewEngine(provider).review(_CTX, _cfg())

        assert len(provider.calls) == 2
        assert provider.security_calls == 1

    def test_an_unsplittable_wall_timeout_is_rescued(self) -> None:
        """A stalled upstream is exactly the transient failure this exists for.

        The adapter refuses to re-send it immediately — an identical request
        against an identical budget can only fail the same way — but a rescue
        issued after the whole wave has drained is a genuinely later request.
        This batch is a single hunk, so there is nothing to split: the rescue is
        the only retry available, and without it the round posts partial.
        """
        provider = _FlakyLens(
            failures=1, exc=ProviderWallTimeout("call exceeded 600s (waited 601.2s)")
        )

        _, summary = LLMReviewEngine(provider).review(_CTX, _cfg())

        assert provider.security_calls == 2
        assert "review calls failed" not in summary

    def test_a_wall_timeout_the_split_already_retried_is_not_rescued(self) -> None:
        """The split IS that call's retry, and it retried with the one change that
        can help — a smaller payload. Rescuing on top would re-send the original
        oversized request at up to twice the calls, each burning a full timeout."""
        provider = _FlakyLens(
            failures=99, exc=ProviderWallTimeout("call exceeded 600s (waited 601.2s)")
        )
        two_files = PRContext(
            diff=(
                "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                "@@ -1,1 +1,2 @@\n context\n+one\n"
                "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n"
                "@@ -1,1 +1,2 @@\n context\n+two\n"
            ),
            changed_files=["a.py", "b.py"],
            base_sha="abc",
            head_sha="def",
            repo="org/repo",
            pr_number=1,
        )

        _, summary = LLMReviewEngine(provider).review(two_files, _cfg())

        # The security call, then its two halves — and stop. No fourth call.
        assert provider.security_calls == 3
        assert INCOMPLETE_MARKER in summary

    def test_a_truncated_response_is_not_rescued(self) -> None:
        """A blown output ceiling is deterministic, not an outage: the same
        request runs to the same ceiling. Rescuing it would buy a second
        identical failure at full generation cost."""
        provider = _FlakyLens(
            failures=99,
            exc=ProviderTruncated(
                "response hit the 16384-token `max_tokens` ceiling before finishing",
                text="",
                output_tokens=16384,
            ),
        )

        _, summary = LLMReviewEngine(provider).review(_CTX, _cfg())

        # A single hunk: nothing smaller to try, and no rescue either.
        assert provider.security_calls == 1
        assert INCOMPLETE_MARKER in summary

    def test_unparseable_output_is_not_rescued(self) -> None:
        """Determinism cuts both ways: at temperature 0 the same request returns
        the same unparseable answer, so a rescue would only buy a second billed
        failure. Left to the incomplete notice instead."""

        class _Gibberish(FakeProvider):
            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                prompt = "\n".join(str(m.get("content", "")) for m in messages)
                self.calls.append({"messages": messages, "model": model, "opts": opts})
                text = "not json at all" if _SECURITY_MARKER in prompt else _FINDING_JSON
                return ProviderResult(text=text, input_tokens=1, output_tokens=1)

        provider = _Gibberish()

        _, summary = LLMReviewEngine(provider).review(_CTX, _cfg())

        assert len(provider.calls) == 2  # no third call
        assert "unparseable model output" in summary

    def test_a_deadline_skipped_call_is_not_rescued(self) -> None:
        """A ceiling the user set is not a fault to retry past.

        Serial provider, a deadline shorter than one call: the first call runs,
        the deadline passes, the second is skipped. The rescue must leave the
        skipped one alone — spending past `max_review_seconds` to rescue a call
        the deadline stopped would defeat the deadline.
        """
        import time as _time

        class _Slow(FakeProvider):
            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                self.calls.append({"messages": messages, "model": model, "opts": opts})
                _time.sleep(1.2)
                return ProviderResult(text=_FINDING_JSON, input_tokens=1, output_tokens=1)

        provider = _Slow()
        # ollama resolves to one worker, so the calls are strictly serial.
        cfg = _cfg(provider=Provider.ollama, max_review_seconds=1)

        _, summary = LLMReviewEngine(provider).review(_CTX, cfg)

        assert len(provider.calls) == 1
        assert "deadline" in summary

    def test_the_notice_names_the_failing_lens(self) -> None:
        """A failed security lens and a failed documentation lens read identically
        as a bare count. Naming it is the difference between "re-run this" and
        "this review is fine"."""
        provider = _FlakyLens(failures=99)

        _, summary = LLMReviewEngine(provider).review(_CTX, _cfg())

        assert "security" in summary
