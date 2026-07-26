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
from dataclasses import dataclass, field
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
            error=error,
        )
        with self._lock:
            self.calls.append(record)
        _log.info(
            "provider call",
            extra={
                "label": label,
                "batch": batch,
                "elapsed_s": round(elapsed, 3),
                "attempts": attempts,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_creation_tokens": cache_creation_tokens,
                **({"error": error} if error else {}),
            },
        )

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

        lines.append(
            f"{'call':<16} {'batch':>5} {'tries':>5} {'elapsed':>9} "
            f"{'in_tok':>8} {'out_tok':>8} {'cache_rd':>8} {'cache_wr':>8}  error"
        )
        for call in sorted(calls, key=lambda c: c.elapsed, reverse=True):
            lines.append(
                f"{call.label:<16} {call.batch:>5} {call.attempts:>5} {call.elapsed:>8.2f}s "
                f"{call.input_tokens:>8} {call.output_tokens:>8} "
                f"{call.cache_read_tokens:>8} {call.cache_creation_tokens:>8}  "
                f"{call.error or '-'}"
            )
        lines.append("")

        read = sum(c.cache_read_tokens for c in calls)
        created = sum(c.cache_creation_tokens for c in calls)
        lines.append(f"cache: {read} tokens read / {created} created across {len(calls)} calls")
        return "\n".join(lines)


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
        profiler.record_call(
            label=label,
            batch=batch,
            elapsed=time.perf_counter() - started,
            attempts=attempts_of(exc),  # 0 only if it never reached the retry loop
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            error=f"{type(exc).__name__}",
        )
        raise
    profiler.record_call(
        label=label,
        batch=batch,
        elapsed=time.perf_counter() - started,
        attempts=result.attempts,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
    )
    return result
