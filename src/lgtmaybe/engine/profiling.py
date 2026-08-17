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

# Bumped when `Profiler.as_dict`'s shape changes in a way a pinned consumer
# would notice. Adding a field is not such a change; removing or re-meaning one
# is. Nothing else in the repo emits a versioned payload — this output exists to
# be pinned against, which is why it starts one.
_PROFILE_SCHEMA_VERSION = 1


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
    # Thinking tokens inside `output_tokens`. None = the route reported no
    # breakdown, which is NOT the same as zero and must not render as one.
    # Keyword-defaulted so every existing caller keeps working unchanged.
    reasoning_tokens: int | None = None
    # The `max_tokens` ceiling this call was sent with (None = uncapped). The
    # denominator of the reasoning share — the figure that answers "does this
    # pair of settings have headroom on the real workload?", which before this
    # only became visible when a call FAILED, in the truncation message.
    output_ceiling: int | None = None
    # Parsed review findings. None for non-review calls and failures; zero is a
    # successful lens that explicitly returned an empty findings payload.
    findings: int | None = None

    @property
    def reasoning_share(self) -> float | None:
        """Reasoning as a fraction of the ceiling, or None when either is unknown."""
        if not self.reasoning_tokens or not self.output_ceiling:
            return None
        return self.reasoning_tokens / self.output_ceiling


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
    returned_findings: int | None = None

    def reset(self) -> None:
        """Start a fresh run: drop prior records and restart the wall clock."""
        with self._lock:
            self.calls = []
            self.stages = []
            self.returned_findings = None
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
        reasoning_tokens: int | None = None,
        output_ceiling: int | None = None,
        findings: int | None = None,
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
            output_ceiling=output_ceiling,
            findings=findings,
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
        if output_ceiling is None:
            del extra["output_ceiling"]
        if findings is None:
            del extra["findings"]
        if not error:
            del extra["error"]
        _log.info("provider call", extra=extra)

    def record_result(
        self,
        label: str,
        batch: int,
        elapsed: float,
        result: ProviderResult,
        *,
        findings: int | None = None,
        error: str | None = None,
    ) -> None:
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
            output_ceiling=result.output_ceiling,
            findings=findings,
            error=error,
        )

    def record_returned_findings(self, count: int) -> None:
        """Record how many findings survived the review pipeline."""
        with self._lock:
            self.returned_findings = count

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
            reasoning_tokens=getattr(exc, "reasoning_tokens", None),
            # A truncation's ceiling IS its output_tokens — it generated all the
            # way to it — so the share it reports is 100% by construction, which
            # is exactly the diagnosis.
            output_ceiling=getattr(exc, "output_tokens", None),
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

    def as_dict(self) -> dict[str, Any]:
        """The profile as data, for a consumer that should not parse the table.

        The table is a human artefact, and its one correctness decision — `-` for
        an unknown reasoning count, never `0`, because 0 asserts the model did no
        thinking — is exactly what breaks a naive `int()` on the column. A
        downstream harness lost roughly a tenth of every call row it recorded to
        that. So unknown stays ``None`` here and serialises to JSON `null`: still
        not zero, but no longer something to string-match.

        ``reasoning_share`` is the ratio the table rounds into an integer percent;
        a consumer wants the number it was rounded from.

        ``schema_version`` is a new convention rather than an existing one —
        nothing else in the repo emits a versioned payload — and it is here
        because the whole point of this output is that it can be pinned against.
        """
        with self._lock:
            calls, stages = list(self.calls), list(self.stages)
            returned = self.returned_findings
            wall = time.perf_counter() - self._started
        return {
            "schema_version": _PROFILE_SCHEMA_VERSION,
            "wall_seconds": wall,
            "total_tokens": sum(c.input_tokens + c.output_tokens for c in calls),
            "returned_findings": returned,
            "stages": [{"name": s.name, "elapsed": s.elapsed} for s in stages],
            "calls": [{**asdict(call), "reasoning_share": call.reasoning_share} for call in calls],
        }

    def render(self) -> str:
        """The ``--profile`` summary as plain text (reads well in an Action log)."""
        with self._lock:
            total = time.perf_counter() - self._started
            calls = list(self.calls)
            stages = list(self.stages)
            returned_findings = self.returned_findings

        lines = ["== lgtmaybe profile ==", f"total wall time: {total:.1f}s", ""]

        lines.append(f"{'stage':<16} {'elapsed':>9}")
        for stage in stages:
            lines.append(f"{stage.name:<16} {stage.elapsed:>8.2f}s")
        lines.append("")

        # `think_tok` sits next to `out_tok` deliberately: side by side they are
        # the whole story of a lens that ran for a minute and wrote 50 tokens.
        # `think_%` is that count against the `max_tokens` ceiling it came out
        # of — the ratio, not either raw number, is what says whether this pair
        # of settings still has headroom on the real workload.
        lines.append(
            f"{'call':<16} {'batch':>5} {'tries':>5} {'elapsed':>9} "
            f"{'in_tok':>8} {'out_tok':>8} {'think_tok':>9} {'think_%':>8} "
            f"{'cache_rd':>8} {'cache_wr':>8} {'findings':>8}  error"
        )
        for call in sorted(calls, key=lambda c: c.elapsed, reverse=True):
            # A dash, never a zero, on both: a route that reported no breakdown
            # did not tell us the model thought nothing, and an uncapped call has
            # no denominator to be a share of.
            thinking = "-" if call.reasoning_tokens is None else f"{call.reasoning_tokens}"
            share = call.reasoning_share
            percent = "-" if share is None else f"{round(100 * share)}%"
            findings = "-" if call.findings is None else str(call.findings)
            lines.append(
                f"{call.label:<16} {call.batch:>5} {call.attempts:>5} {call.elapsed:>8.2f}s "
                f"{call.input_tokens:>8} {call.output_tokens:>8} {thinking:>9} {percent:>8} "
                f"{call.cache_read_tokens:>8} {call.cache_creation_tokens:>8} "
                f"{findings:>8}  {call.error or '-'}"
            )
        lines.append("")

        read = sum(c.cache_read_tokens for c in calls)
        created = sum(c.cache_creation_tokens for c in calls)
        lines.append(f"cache: {read} tokens read / {created} created across {len(calls)} calls")
        parsed = [c.findings for c in calls if c.findings is not None]
        if parsed or returned_findings is not None:
            returned = "-" if returned_findings is None else str(returned_findings)
            lines.append(f"findings: {sum(parsed)} parsed / {returned} returned")
        for extra_line in (self._render_reasoning(calls), self._render_headroom(calls)):
            if extra_line:
                lines.append(extra_line)
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

        Both sums are restricted to the calls that REPORTED a breakdown. Dividing
        known reasoning by every call's output mixes a measurement with a silence
        and understates the share by however much the unreporting calls
        generated — badly, when they generated most of it. Whether every call is
        counted is said out loud rather than left for the reader to assume.
        """
        measured = [c for c in calls if c.reasoning_tokens is not None]
        if not measured:
            # Nothing REPORTED — the only case with nothing to say. A run whose
            # routes all reported 0 has measured something (a model that did no
            # thinking), and suppressing it here would put it back in the same
            # bucket as silence, which is the distinction this line exists for.
            return ""
        reasoning = sum(c.reasoning_tokens or 0 for c in measured)
        output = sum(c.output_tokens for c in measured)
        share = round(100 * reasoning / output) if output else 0
        line = f"reasoning: {reasoning:,} of {output:,} output tokens ({share}%)"
        if len(measured) == len(calls):
            return line
        return f"{line[:-1]}, across {len(measured)} of {len(calls)} calls reporting it)"

    @staticmethod
    def _render_headroom(calls: list[CallRecord]) -> str:
        """The largest reasoning share any single call reached — "" when unknown.

        The one line that answers the question the raw counts do not: is the
        configured `max_tokens` / `reasoning_effort` pair close to the edge on
        this workload? An aggregate share hides it — a run whose lenses average
        20% can still contain the call that spent the whole ceiling thinking and
        wrote nothing, which is exactly the failure this exists to make visible
        before it happens rather than after.

        The call is named, because "which lens" is the next question and the
        table is long.
        """
        scored = [(c.reasoning_share, c) for c in calls if c.reasoning_share is not None]
        if not scored:
            return ""
        share, call = max(scored, key=lambda pair: pair[0] or 0.0)
        return (
            f"largest reasoning share: {round(100 * (share or 0))}% of the "
            f"{call.output_ceiling:,}-token ceiling ({call.reasoning_tokens:,} tokens, "
            f"{call.label}, batch {call.batch})"
        )

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
