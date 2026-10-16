"""One review's run: the settings every model call shares, and its record.

Split out of ``engine.py`` so the preparation stages (``stages.py``), the
recovery ladder (``recovery.py``) and the summary notices (``notices.py``) can
all read the same run without importing the orchestrator. Holds the per-run
types (:class:`_Run`, :class:`_RunState`, :class:`_MeteredProvider`), the
ceilings every call re-checks (:func:`_skip_reason`, the interrupt switch), and
what one lens call may contribute (:func:`_stamp_and_bound`).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Protocol

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    ProviderResult,
    ReviewConfig,
    ReviewFinding,
    ReviewResult,
    schema_dropped_on,
    temperature_dropped_on,
)
from lgtmaybe.core.ports import Message, ProviderClient

from .lenses import _Lens
from .selftalk import drop_self_talk
from .severity import clamp_to_category_ceiling

# The engine's logger, shared by every module the engine is split across (run,
# lenses, stages, recovery, notices): a log line keeps the logger name it had
# when all of this lived in engine.py, so a log filter keyed on it still matches.
_log = get_logger("lgtmaybe.engine.engine")

# The reason string a call skipped by the token ceiling reports. Shared with the
# summary step, which counts these to tell a budget stop (spend the user chose)
# apart from the provider failures the generic incomplete notice covers.
_BUDGET_SKIP_REASON = "token budget (max_review_tokens) reached: call skipped"


# The reason string a call skipped by an interruption reports. Worded to name
# the cause, because the remedy differs from the deadline's: nobody should go
# hunting for a `max_review_seconds` they never hit.
_INTERRUPT_SKIP_REASON = "review interrupted (termination signal): call skipped"

# A wind-down asked for from OUTSIDE the pipeline — in practice the CLI's
# SIGTERM/SIGINT handler, when the CI job blows its `timeout-minutes` or a push
# cancels the run. Deliberately the same state the elapsed deadline sets: queued
# model calls are skipped, in-flight ones finish, and the review posts partial
# results with a notice instead of dying with nothing on the PR. Process-global
# and never cleared by `review()` — a signal means stop, not stop-for-one-review
# — so the CLI's other steps wind down too. `Event` because the fan-out reads it
# from every worker thread.
_interrupted = threading.Event()


def request_interrupt() -> None:
    """Stop starting new model calls; post what the review already has."""
    _interrupted.set()


def interrupt_requested() -> bool:
    """Whether a wind-down has been asked for."""
    return _interrupted.is_set()


def clear_interrupt() -> None:
    """Forget a requested wind-down (tests, and a long-lived host process)."""
    _interrupted.clear()


# One prepared _review_lens call, ready to submit to the fan-out pool.
_LensOutcome = tuple[list[ReviewFinding], str | None]
# Builds the (intent, hint, directory, spec) blocks for one set of file paths:
# a batch's own set, or the subset a split piece shows.
_BlocksFor = Callable[[set[str]], tuple[str | None, str | None, str | None, str | None]]
_ReviewTask = partial[_LensOutcome]


@dataclass(frozen=True)
class _PreparedCall:
    """One lens call ready to submit, and the lens it speaks for.

    The lens id travels with the task so a failure can be NAMED — a failed
    security lens and a failed documentation lens read identically as a bare
    count, and they are not remotely the same news.
    """

    lens_id: str
    task: _ReviewTask


@dataclass
class _RunState:
    """What one review has done so far: the facts its summary notices report,
    and the billable tokens its model calls have spent.

    Built fresh for every review and carried on its :class:`_Run`, never on the
    engine: one engine may serve two reviews at once, and each summary must
    describe its own run alone. Written from the fan-out's worker threads.

    The sets and ``escalated`` are written directly: a set's ``add`` and a
    dict's plain store are single operations, so they need no lock. The
    counters (``flooded``, ``self_talk`` and the token tally) are a read, an
    add and a store, so they change only through the methods below, which hold
    the lock themselves; the two degradation flags go the same way. No caller
    ever takes it by hand.
    """

    # Batches an oversized-payload failure (wall timeout or output ceiling)
    # forced us to review in smaller pieces.
    split_batches: set[int] = field(default_factory=set)
    # Lenses that only answered after their reasoning effort was stepped down
    # (see _retry_lower_effort). Keyed by lens so the notice counts lenses,
    # not calls — the same lens stepping down in two batches is one fact
    # about the review, not two.
    stepped_down: set[str] = field(default_factory=set)
    # Lenses that answered only with reasoning switched off (the rung below
    # the step-down); kept apart because the summary makes a different claim.
    reasoning_off: set[str] = field(default_factory=set)
    # Lenses a SECOND model answered, keyed to the model that answered them.
    # Two paths land here and the reader cannot tell them apart, which is the
    # point: the engine escalating a truncation itself (see _escalate_model),
    # and the adapter switching model on its own for any other failure. Both
    # are read off the answering model on the result rather than recorded at
    # the call site, so neither can be disclosed and the other not. A plain
    # store needs no lock: two batches racing here agree on the value anyway.
    escalated: dict[str, str] = field(default_factory=dict)
    # Lenses whose unparseable reply was reformatted into findings by a
    # second call (see repair.py). Keyed by lens for the same reason as
    # `stepped_down`: one fact about the review, not one per batch.
    repaired: set[str] = field(default_factory=set)
    # Lenses that only parsed once the provider's JSON schema was taken off
    # the request (see _retry_without_schema). Keyed by lens likewise.
    re_asked: set[str] = field(default_factory=set)
    # Findings dropped per lens by the per-lens bound (see add_flooded). Two
    # batches' primers flooding at once would otherwise lose an update and
    # under-report the drop in the summary notice.
    flooded: dict[str, int] = field(default_factory=dict)
    # Same shape: findings dropped per lens for being the model's note to
    # itself (see add_self_talk, selftalk.py and _stamp_and_bound).
    self_talk: dict[str, int] = field(default_factory=dict)
    # Whether any call of this review went out without the structured-output
    # schema it asked for, or without the configured temperature. Read off each
    # call's result (or a failed call's exception) as it returns, never asked of
    # the adapter afterwards: the adapter is shared, and "dropped this run" there
    # would mean every review it has ever served. Set only through
    # note_degradation, and only ever to True.
    schema_dropped: bool = False
    temperature_dropped: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    # Read and written only through charge() and tokens_spent().
    _tokens: int = field(default=0, init=False)

    def add_flooded(self, lens_id: str, dropped: int) -> None:
        """Count *dropped* findings the per-lens bound cut from *lens_id*."""
        with self._lock:
            self.flooded[lens_id] = self.flooded.get(lens_id, 0) + dropped

    def add_self_talk(self, lens_id: str, dropped: int) -> None:
        """Count *dropped* self-talk findings removed from *lens_id*."""
        with self._lock:
            self.self_talk[lens_id] = self.self_talk.get(lens_id, 0) + dropped

    def charge(self, tokens: int) -> None:
        """Add one model call's billable tokens to this review's spend."""
        with self._lock:
            self._tokens += tokens

    def note_degradation(self, *, schema_dropped: bool, temperature_dropped: bool) -> None:
        """Record what one call lost on the way to the model."""
        with self._lock:
            self.schema_dropped = self.schema_dropped or schema_dropped
            self.temperature_dropped = self.temperature_dropped or temperature_dropped

    def tokens_spent(self) -> int:
        """Billable tokens (input + output) this review's calls have spent.

        The figure `max_review_tokens` is enforced against. Input + output only,
        like :meth:`Profiler.total_tokens`, and for the same reason: it is the
        one count every provider route agrees on.
        """
        with self._lock:
            return self._tokens


class _MeteredProvider:
    """The review's provider, charging every call's tokens to that review.

    Every model call a review makes goes through this one object: the lens
    calls and their retries, split pieces and rescues, the repair re-ask,
    triage and reflection. So the run's tally sees exactly the calls the
    profiler records, without a tally parameter threaded through every module
    that makes one. A failed call is charged what it reports spending, the way
    the profiler's ``record_error`` charges it: a truncation reached the model
    and is usually the costliest call of the run. What each call lost (its
    schema, its temperature) is recorded on the same pass, from the result or
    the failed call's exception, for the same reason. Recovery probes stay on
    the engine's own provider; this wrapper only speaks the ``ProviderClient``
    port.
    """

    def __init__(self, inner: ProviderClient, state: _RunState) -> None:
        self._inner = inner
        self._state = state

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        try:
            result = self._inner.complete(messages, model=model, **opts)
        except Exception as exc:
            spent = getattr(exc, "input_tokens", None) or 0
            spent += getattr(exc, "output_tokens", None) or 0
            self._state.charge(spent)
            self._state.note_degradation(
                schema_dropped=schema_dropped_on(exc),
                temperature_dropped=temperature_dropped_on(exc),
            )
            raise
        self._state.charge(result.input_tokens + result.output_tokens)
        self._state.note_degradation(
            schema_dropped=result.schema_dropped,
            temperature_dropped=result.temperature_dropped,
        )
        return result


@dataclass(frozen=True)
class _Run:
    """The settings that are fixed for a whole review, resolved once per run.

    Every model call in the fan-out — the first one, an oversized batch's split
    pieces, a deferral's re-run — reads exactly the same values, so they travel
    as one object rather than as seven parameters re-declared at each hop.

    It also carries the review's :class:`_RunState`. The reference is fixed for
    the run like every other field; the object it points at is the run's
    mutable record. It rides here rather than as a second parameter because
    every hop that reads a ceiling also records an outcome, and a parallel
    argument would be re-declared at each of them.
    """

    model: str
    # Constrains output to the findings schema (provider-native JSON mode) on
    # review calls only — the reflection call keeps its own format.
    response_format: type[ReviewResult] | None
    language: str | None
    # The rendered project paragraph (`prompt.project_context`), "" when unset.
    project: str
    deadline_at: float | None
    budget_at: int | None
    # Mid-review retrieval budget in tokens, or None when a lens may not defer.
    retrieval_budget: int | None
    # How many calls this backend will serve at once (concurrency_cap). Carried
    # for the oversized-batch split, which runs its pieces in a pool of its own
    # and must not out-run what the fan-out itself is allowed.
    concurrency: int
    # The whole config, for the settings a call reads only on its failure path
    # (the repair re-ask) — carried rather than re-threaded as parameters that
    # every hop between review() and _complete_lens would have to re-declare.
    cfg: ReviewConfig
    # The provider every model call of this review goes through: the engine's
    # own, metered into `state` (see _MeteredProvider).
    provider: ProviderClient
    # This review's notices and spend; see _RunState.
    state: _RunState
    # The PR's full changed-file list. Carried for the same reason as
    # `concurrency`: a split piece shows FEWER files than the batch it came from,
    # so it has to derive its own not-shown manifest rather than inherit the
    # batch's — which would leave a piece silently unaware of its siblings' files.
    changed_files: tuple[str, ...] = ()
    # The PR's place in its stack (`injection.wrap_stack`), redacted, or None
    # outside a stack. The same for every batch and lens, so it rides the shared
    # prefix of every call, split pieces and deferral re-runs included.
    stack_block: str | None = None


class _OnOversized(Protocol):
    """The split a lens call may fall back to: ``(reason, slice_hunks=...)``.

    A Protocol rather than a ``Callable`` alias because the second argument is
    keyword-only, which ``Callable[...]`` cannot express.
    """

    def __call__(self, reason: str, *, slice_hunks: bool = ...) -> _LensOutcome: ...


def _skip_reason(run: _Run, lens: _Lens) -> str | None:
    """Why this model call must not be made, or None to go ahead.

    Every ceiling is checked at EXECUTION time (tasks queue in the pool long
    before a worker picks them up, and a deferral is decided later still), so the
    figures include everything that landed in the meantime. One function, so the
    first call and a deferral's re-run can never drift apart on when to stop.
    The spend is this run's own tally, never the process-wide profiler, so a
    review running beside another is charged only for its own calls.
    """
    if interrupt_requested():
        _log.warning("review interrupted — skipping call", extra={"lens": lens.id})
        return _INTERRUPT_SKIP_REASON
    if run.deadline_at is not None and time.perf_counter() >= run.deadline_at:
        _log.warning("review deadline reached — skipping call", extra={"lens": lens.id})
        return "review deadline (max_review_seconds) reached: call skipped"
    if run.budget_at is not None and run.state.tokens_spent() >= run.budget_at:
        _log.warning("review token budget reached — skipping call", extra={"lens": lens.id})
        return _BUDGET_SKIP_REASON
    return None


def _stamp_and_bound(findings: list[ReviewFinding], lens: _Lens, run: _Run) -> list[ReviewFinding]:
    """Stamp the originating lens, then bound what one call may contribute.

    Both apply to every (batch, lens) result: the normal path, a repaired
    reply and a salvaged truncation. They are applied here rather than at
    each return so the three paths cannot diverge. The bound is a backstop
    against a degenerate response rather than a review budget; see
    `ReviewConfig.max_findings_per_lens`.
    """
    findings = _stamp_categories(findings, lens)
    # A finding whose prose is the model's note to itself about its output
    # format is corrupted output, not a review — and it is imperative text,
    # which must never reach a thread that agents read as instructions.
    # Dropped first so it cannot take a slot from a genuine finding below.
    findings, self_talk = drop_self_talk(findings)
    if self_talk:
        run.state.add_self_talk(lens.id, len(self_talk))
        _log.warning(
            "dropped findings carrying the model's own repair note",
            extra={
                "lens": lens.id,
                "dropped": len(self_talk),
                "titles": [f.title[:120] for f in self_talk],
            },
        )
    cap = run.cfg.max_findings_per_lens
    if not cap or len(findings) <= cap:
        return findings
    # Highest severity first, input order preserved within a severity, so
    # what survives is deterministic and provider-independent. This is the
    # same selection policy `_dedupe` uses.
    kept = sorted(findings, key=lambda f: -f.severity.rank)[:cap]
    dropped = len(findings) - len(kept)
    run.state.add_flooded(lens.id, dropped)
    _log.warning(
        "lens exceeded the per-lens finding bound — dropping the least severe",
        extra={"lens": lens.id, "returned": len(findings), "kept": cap, "dropped": dropped},
    )
    return kept


def _stamp_categories(findings: list[ReviewFinding], lens: _Lens) -> list[ReviewFinding]:
    """Stamp the originating lens on each finding (engine-derived).

    It drives the security label and category-matched `finding_rules`, and
    surfaces in JSON output. A focused lens overwrites the model's value
    outright; a merged (fast-preset) lens covers several categories, so a
    model-supplied value from its member set is kept and anything else falls
    back to the lens id. Applied to salvaged findings too — a finding recovered
    from a truncated response is posted like any other, so it must be labelled
    like any other.
    """
    allowed = lens.allowed_categories
    stamped = [
        f.model_copy(
            update={
                "category": (
                    f.category if allowed is not None and f.category in allowed else lens.id
                )
            }
        )
        for f in findings
    ]
    # Now that each finding knows which lens it came from, hold the advisory
    # ones to the grade the prompt asked them for. Before the per-lens bound
    # below, which drops the least severe first: an over-graded nit would
    # otherwise survive at the expense of a real finding.
    return clamp_to_category_ceiling(stamped)
