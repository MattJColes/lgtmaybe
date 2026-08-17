"""Tests for the rescue wave: one more go at a call that failed transiently.

A single flaky provider call used to void a whole round's verdict — three
consecutive reviews each reported "1 of 4 review calls failed" while the other
three lenses succeeded, and the findings the failed lens would have made were
lost until somebody re-ran by hand. The rescue wave re-runs exactly those calls
once, after the main wave has drained, and costs nothing at all on a healthy run.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
    stamp_unrecoverable,
)
from lgtmaybe.core.ports import Message, ProviderTruncated, ProviderWallTimeout
from lgtmaybe.engine import (
    INCOMPLETE_MARKER,
    LLMReviewEngine,
    clear_interrupt,
    request_interrupt,
)
from lgtmaybe.engine import engine as engine_module
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


class _InterruptOnFirstCall(FakeProvider):
    """Raises the wind-down flag from inside its first completion."""

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        request_interrupt()
        return ProviderResult(text=_FINDING_JSON, input_tokens=1, output_tokens=1)


@pytest.fixture(autouse=True)
def _clean_interrupt_flag() -> Iterator[None]:
    """The flag is process-global: never let one test leak into the next."""
    clear_interrupt()
    yield
    clear_interrupt()


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

    def test_a_piece_that_fails_on_the_provider_is_still_rescued(self) -> None:
        """The split answers a SIZE problem. A 429 in a piece is not one.

        Stripping the retryable marker from every split failure conflated the
        two: "the smaller payload also timed out" (nothing left to try) and "a
        piece hit a capacity 429" (the provider faltered, and one more go is
        exactly what the rescue wave is for). The second was silently excluded.

        Here the whole batch times out, the split runs, both pieces fail
        transiently — and the lens must still get its one rescue.
        """
        calls = {"n": 0}
        lock = threading.Lock()

        class _StallsThenFlaky(FakeProvider):
            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                prompt = "\n".join(str(m.get("content", "")) for m in messages)
                with lock:
                    self.calls.append({"messages": messages, "model": model, "opts": opts})
                    if _SECURITY_MARKER not in prompt:
                        return ProviderResult(text=_FINDING_JSON, input_tokens=1, output_tokens=1)
                    calls["n"] += 1
                    n = calls["n"]
                if n == 1:
                    raise ProviderWallTimeout("call exceeded 600s (waited 601.2s)")
                if n in (2, 3):  # the two split pieces
                    raise RuntimeError("upstream hiccup")
                return ProviderResult(text=_FINDING_JSON, input_tokens=1, output_tokens=1)

        provider = _StallsThenFlaky()
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

        findings, summary = LLMReviewEngine(provider).review(two_files, _cfg())

        # whole batch, two pieces, then the rescue — which succeeds.
        assert calls["n"] == 4
        assert "review calls failed" not in summary
        # And the rescue's answer was actually kept: a rescue that made the call
        # and discarded the response would satisfy everything above.
        assert any(f.path == "a.py" for f in findings)

    def test_one_provider_failure_among_the_pieces_is_enough(self) -> None:
        """Retryability is a property of ANY piece, the message is the last one.

        Reading both off `errors[-1]` conflates them: a piece that failed on the
        provider followed by one that ran out of room reports a payload reason
        last, and the provider failure — the case this rescue exists for —
        silently loses its turn behind it.
        """
        seen = {"n": 0}
        lock = threading.Lock()

        class _MixedPieceFailures(FakeProvider):
            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                prompt = "\n".join(str(m.get("content", "")) for m in messages)
                with lock:
                    self.calls.append({"messages": messages, "model": model, "opts": opts})
                    if _SECURITY_MARKER not in prompt:
                        return ProviderResult(text=_FINDING_JSON, input_tokens=1, output_tokens=1)
                    seen["n"] += 1
                    n = seen["n"]
                if n == 1:
                    raise ProviderWallTimeout("call exceeded 600s (waited 601.2s)")
                # The pieces, in submission order: a.py transiently, then b.py on
                # size — so the PAYLOAD reason is the one that lands last.
                if n == 2:
                    raise RuntimeError("upstream hiccup")
                if n == 3:
                    raise ProviderWallTimeout("call exceeded 600s (waited 601.2s)")
                return ProviderResult(text=_FINDING_JSON, input_tokens=1, output_tokens=1)

        provider = _MixedPieceFailures()
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

        assert seen["n"] == 4  # the rescue still happened
        assert "review calls failed" not in summary

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

    def test_an_unrecoverable_provider_failure_is_not_rescued(self) -> None:
        """A dead key or a spent quota cannot come back mid-review, so a rescue
        would only pay a second billed call to be told the same thing. The
        adapter already knows this and stamps the exception; the engine reads it
        rather than deciding again for itself.
        """
        exc = RuntimeError("insufficient_quota — you exceeded your current quota")
        stamp_unrecoverable(exc)
        provider = _FlakyLens(failures=99, exc=exc)

        _, summary = LLMReviewEngine(provider).review(_CTX, _cfg())

        assert provider.security_calls == 1
        assert INCOMPLETE_MARKER in summary

    def test_unparseable_output_is_not_rescued(self) -> None:
        """Determinism cuts both ways: at temperature 0 the same request returns
        the same unparseable answer, so a rescue would only buy a second billed
        failure. Left to the incomplete notice instead.

        Both recoveries off, so this pins the rescue rule alone — each of them
        sends a DIFFERENT request (the reformat re-ask carries no diff, the
        schema-less re-run drops a parameter) and each has its own suite."""

        class _Gibberish(FakeProvider):
            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                prompt = "\n".join(str(m.get("content", "")) for m in messages)
                self.calls.append({"messages": messages, "model": model, "opts": opts})
                text = "not json at all" if _SECURITY_MARKER in prompt else _FINDING_JSON
                return ProviderResult(text=text, input_tokens=1, output_tokens=1)

        provider = _Gibberish()

        _, summary = LLMReviewEngine(provider).review(
            _CTX, _cfg(repair_unparseable=False, retry_without_schema=False)
        )

        assert len(provider.calls) == 2  # no third call
        assert "unparseable model output" in summary

    def test_the_extra_call_after_unparseable_output_is_a_reformat_not_a_rescue(self) -> None:
        """The boundary between the two mechanisms, stated as a test.

        A rescue re-issues the SAME request, which is why unparseable output is
        excluded from it. The repair re-ask sends the model's own reply back with
        the schema and no diff — a different request, and the only one of the two
        that can recover anything here."""

        class _Gibberish(FakeProvider):
            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                prompt = "\n".join(str(m.get("content", "")) for m in messages)
                self.calls.append({"messages": messages, "model": model, "opts": opts})
                text = "not json at all" if _SECURITY_MARKER in prompt else _FINDING_JSON
                return ProviderResult(text=text, input_tokens=1, output_tokens=1)

        provider = _Gibberish()
        LLMReviewEngine(provider).review(_CTX, _cfg())

        assert len(provider.calls) == 3
        prompts = [
            "\n".join(str(m.get("content", "")) for m in call["messages"])
            for call in provider.calls
        ]
        # Found by content, not by position: the repair runs inline in its own
        # lens's thread, so it need not be the last call to land.
        reformats = [p for p in prompts if "not json at all" in p]
        assert len(reformats) == 1
        assert _SECURITY_MARKER not in reformats[0], "a re-run of the lens, not a reformat"

    def test_a_ceiling_still_holds_through_the_rescue_wave(self) -> None:
        """A ceiling the user set must survive the rescue, not just precede it.

        Worth being precise about what this pins, because it is NOT the
        `_rescuable` gate: a rescue re-enters `_review_lens`, which re-checks
        `_skip_reason` before it calls anything, so an interrupted call costs no
        model call even if the gate let it through. Belt and braces — and this
        is the braces. It fails if the rescue is ever changed to reach the
        provider without going back through that check.

        Driven with the wind-down flag, which reaches `_skip_reason`
        deterministically and instantly; the deadline's route is pinned
        separately below.

        One worker, so the first call raises the flag and the second is
        skipped.
        """
        provider = _InterruptOnFirstCall()

        _, summary = LLMReviewEngine(provider).review(_CTX, _cfg(max_concurrency=1))

        assert len(provider.calls) == 1
        assert "interrupted" in summary

    def test_the_deadline_still_holds_through_the_rescue_wave(self) -> None:
        """The same property for `max_review_seconds` specifically.

        The two ceilings reach `_skip_reason` by different routes — a flag
        versus a clock comparison — and the case above only exercises one of
        them.

        The clock is stepped, not slept: `perf_counter` reports one instant
        until the first completion runs, and a far-future one after. That
        crosses the deadline exactly once, deterministically, with no sleep and
        no dependence on how loaded the runner is.
        """
        crossed = threading.Event()
        base = time.perf_counter()

        class _CrossesTheDeadline(FakeProvider):
            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                self.calls.append({"messages": messages, "model": model, "opts": opts})
                crossed.set()
                return ProviderResult(text=_FINDING_JSON, input_tokens=1, output_tokens=1)

        def stepped() -> float:
            return base + (10_000.0 if crossed.is_set() else 0.0)

        provider = _CrossesTheDeadline()
        # One worker, so the calls are strictly serial: the first runs, crosses
        # the deadline, and the second is skipped by it.
        cfg = _cfg(max_concurrency=1, max_review_seconds=1)
        with patch.object(engine_module.time, "perf_counter", stepped):
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
