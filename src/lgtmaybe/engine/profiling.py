"""Pipeline timing instrumentation.

Every pipeline stage and every provider call records its wall-clock cost here
(``time.perf_counter``) and emits a structured log line as it lands, so a slow
review can be diagnosed from the Action log alone. The collected records also
back the ``--profile`` summary: a per-stage table, a per-call table sorted by
elapsed time, and the prompt-cache totals — the numbers that make a claimed
speed-up provable rather than assumed.

A module-level :data:`profiler` is the collection point (the engine, reflection,
and triage all record into it); the CLI resets it at the start of a run and
renders the summary at the end. Collection is always on — an append under a
lock is far too cheap to gate — only the summary printing is opt-in.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import ProviderResult, attempts_of
from lgtmaybe.core.ports import Message, ProviderClient

_log = get_logger(__name__)


@dataclass(frozen=True)
class CallRecord:
    """One provider completion: what was asked, how long it took, what it cost."""

    label: str  # lens id, or "reflect" / "triage"
    batch: int  # 1-based batch number; 0 for calls outside the per-batch fan-out
    elapsed: float  # seconds, including the adapter's retries
    attempts: int  # completion attempts the adapter made (1 = no retry; 0 = unknown)
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    error: str | None = None  # None on success; the concise failure reason otherwise
    # Thinking tokens inside `output_tokens` (0 = the route reported no breakdown).
    # Keyword-defaulted so every existing caller keeps working unchanged.
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class StageRecord:
    """One pipeline stage's wall-clock cost."""

    name: str
    elapsed: float


@dataclass
class Profiler:
    """Thread-safe collector for stage and call timings (the fan-out is threaded)."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _started: float = field(default_factory=time.perf_counter)
    calls: list[CallRecord] = field(default_factory=list)
    stages: list[StageRecord] = field(default_factory=list)

    def reset(self) -> None:
        """Start a fresh run: drop prior records and restart the wall clock."""
        with self._lock:
            self.calls = []
            self.stages = []
            self._started = time.perf_counter()

    def record_call(
        self,
        *,
        label: str,
        batch: int,
        elapsed: float,
        attempts: int,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_creation_tokens: int,
        reasoning_tokens: int = 0,
        error: str | None = None,
    ) -> None:
        """Record one provider call and log it as a structured line."""
        record = CallRecord(
            label=label,
            batch=batch,
            elapsed=elapsed,
            attempts=attempts,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            reasoning_tokens=reasoning_tokens,
            error=error,
        )
        with self._lock:
            self.calls.append(record)
        extra = asdict(record)
        extra["elapsed_s"] = round(extra.pop("elapsed"), 3)
        # Only when reported: a 0 on every line would read as a claim that the
        # model did no thinking, which is not what it means.
        if not reasoning_tokens:
            del extra["reasoning_tokens"]
        if not error:
            del extra["error"]
        _log.info("provider call", extra=extra)

    def record_result(self, label: str, batch: int, elapsed: float, result: ProviderResult) -> None:
        """Record a successful call: usage counters straight off *result*."""
        self.record_call(
            label=label,
            batch=batch,
            elapsed=elapsed,
            attempts=result.attempts,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_creation_tokens=result.cache_creation_tokens,
            reasoning_tokens=result.reasoning_tokens,
        )

    def record_error(
        self, label: str, batch: int, elapsed: float, exc: BaseException, reason: str | None = None
    ) -> None:
        """Record a failed call, charging it for whatever it actually spent.

        Most failures genuinely cost nothing — a connection error or a rate limit
        never reached the model — and report zeros. A **truncation** is the
        exception that matters: it reached the model and generated all the way to
        the ceiling, so it is routinely the most expensive call in the whole run.
        Reporting it as free made it invisible to :meth:`total_tokens`, and so to
        `max_review_tokens` — the runaway calls the spend ceiling exists to stop
        were precisely the ones it could not see. It also printed the single
        costliest row of ``--profile`` as ``0 / 0``, which is exactly the row a
        reader is looking at.
        """
        self.record_call(
            label=label,
            batch=batch,
            elapsed=elapsed,
            # What the adapter stamped on the exception: a failure that burned its
            # retry budget must not read as one that was never retried. 0 only when
            # the failure never reached the retry loop.
            attempts=attempts_of(exc),
            input_tokens=getattr(exc, "input_tokens", None) or 0,
            output_tokens=getattr(exc, "output_tokens", None) or 0,
            reasoning_tokens=getattr(exc, "reasoning_tokens", None) or 0,
            # A truncation is billed as ordinary input; the route reports no cache
            # breakdown alongside the ceiling error, so claiming one would be
            # invention rather than accounting.
            cache_read_tokens=0,
            cache_creation_tokens=0,
            error=reason or type(exc).__name__,
        )

    def total_tokens(self) -> int:
        """Billable tokens recorded so far: input + output across every call.

        Cache reads/writes are deliberately NOT added on top. Providers differ
        on whether a cached read is already inside the reported prompt count
        (litellm's ``prompt_tokens_details.cached_tokens`` is a subset of
        ``prompt_tokens``; Anthropic reports it alongside), so adding them would
        double-count on some routes and not others. Input + output is the one
        figure every route agrees on — which is what a budget needs to be.

        Backs :attr:`ReviewConfig.max_review_tokens`, so it is read from the
        fan-out threads: the lock is not decoration.
        """
        with self._lock:
            return sum(c.input_tokens + c.output_tokens for c in self.calls)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Time one pipeline stage; records and logs even when the stage raises."""
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - started
            with self._lock:
                self.stages.append(StageRecord(name=name, elapsed=elapsed))
            _log.info("stage completed", extra={"stage": name, "elapsed_s": round(elapsed, 3)})

    def render(self) -> str:
        """The ``--profile`` summary as plain text (reads well in an Action log)."""
        with self._lock:
            total = time.perf_counter() - self._started
            calls = list(self.calls)
            stages = list(self.stages)

        lines = ["== lgtmaybe profile ==", f"total wall time: {total:.1f}s", ""]

        lines.append(f"{'stage':<16} {'elapsed':>9}")
        for stage in stages:
            lines.append(f"{stage.name:<16} {stage.elapsed:>8.2f}s")
        lines.append("")

        # `think_tok` sits next to `out_tok` deliberately: side by side they are
        # the whole story of a lens that ran for a minute and wrote 50 tokens.
        lines.append(
            f"{'call':<16} {'batch':>5} {'tries':>5} {'elapsed':>9} "
            f"{'in_tok':>8} {'out_tok':>8} {'think_tok':>9} {'cache_rd':>8} {'cache_wr':>8}  error"
        )
        for call in sorted(calls, key=lambda c: c.elapsed, reverse=True):
            lines.append(
                f"{call.label:<16} {call.batch:>5} {call.attempts:>5} {call.elapsed:>8.2f}s "
                f"{call.input_tokens:>8} {call.output_tokens:>8} {call.reasoning_tokens:>9} "
                f"{call.cache_read_tokens:>8} {call.cache_creation_tokens:>8}  "
                f"{call.error or '-'}"
            )
        lines.append("")

        read = sum(c.cache_read_tokens for c in calls)
        created = sum(c.cache_creation_tokens for c in calls)
        lines.append(f"cache: {read} tokens read / {created} created across {len(calls)} calls")
        reasoning_line = self._render_reasoning(calls)
        if reasoning_line:
            lines.append(reasoning_line)
        lines.append(self.render_total())
        return "\n".join(lines)

    @staticmethod
    def _render_reasoning(calls: list[CallRecord]) -> str:
        """How much of the output budget went on thought — "" when unreported.

        Silence, not a zero: a route that reports no breakdown is not a model
        that did no thinking, and a line reading "0 of 2,000" would say exactly
        that. The share is computed here rather than left to the reader, because
        it is the ratio — not either raw count — that says whether the output
        ceiling is being spent on findings or on reasoning.
        """
        reasoning = sum(c.reasoning_tokens for c in calls)
        if not reasoning:
            return ""
        output = sum(c.output_tokens for c in calls)
        share = round(100 * reasoning / output) if output else 0
        return f"reasoning: {reasoning:,} of {output:,} output tokens ({share}%)"

    def render_total(self) -> str:
        """One line: what this run spent, formatted for a human.

        Shared by the ``--profile`` table and the local CLI's footer so the two
        can never drift into quoting different numbers for the same run.
        """
        with self._lock:
            calls = list(self.calls)
        billed_in = sum(c.input_tokens for c in calls)
        billed_out = sum(c.output_tokens for c in calls)
        plural = "" if len(calls) == 1 else "s"
        return (
            f"tokens: {billed_in + billed_out:,} billable "
            f"({billed_in:,} in / {billed_out:,} out) across {len(calls)} call{plural}"
        )


# The process-wide collection point. One review runs per CLI invocation, so a
# module-level instance (reset per run) is the simplest thing that works — the
# alternative, threading a profiler through every pipeline signature, buys
# nothing but churn.
profiler = Profiler()


def timed_complete(
    provider: ProviderClient,
    messages: list[Message],
    *,
    model: str,
    label: str,
    batch: int = 0,
    **opts: Any,
) -> ProviderResult:
    """Run one provider completion, recording its timing/usage under *label*.

    The single instrumentation point for provider calls outside the per-lens
    fan-out (reflection, triage). Errors re-raise unchanged after being
    recorded, so callers keep their existing failure semantics.
    """
    started = time.perf_counter()
    try:
        result = provider.complete(messages, model=model, **opts)
    except Exception as exc:
        profiler.record_error(label, batch, time.perf_counter() - started, exc)
        raise
    profiler.record_result(label, batch, time.perf_counter() - started, result)
    return result
