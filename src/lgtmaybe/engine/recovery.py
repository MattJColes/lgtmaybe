"""The recovery policy for one lens call: the truncation ladder and its siblings.

Split out of ``engine.py`` so the orchestrator only decides *that* a call
failed; what to try next lives here. A cut-off or oversized call works the
truncation ladder (:meth:`_Ladder._remedy_oversized`): shrink the payload
(:meth:`_Ladder._review_split`) or step the thinking down
(:meth:`_Ladder._retry_lower_effort`), and only then change the model
(:meth:`_Ladder._escalate_model`). An unparseable reply gets the reformat
re-ask and then one re-run without the schema
(:meth:`_Ladder._retry_without_schema`). After the fan-out, :func:`_rescue`
gives each provider-side failure one more go.

The seam back into the engine is explicit and narrow: a :class:`_Ladder`
holds the adapter's recovery steps and two callbacks, :class:`_Reissue` (one
more call of the same lens, with no ladder of its own) and
:class:`_ReviewPiece` (one split piece, which is never split again).
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    ProviderResult,
    ReviewFinding,
    ReviewResult,
    is_unrecoverable,
)
from lgtmaybe.core.ports import (
    Message,
    ProviderInputTooLarge,
    ProviderTruncated,
    ProviderWallTimeout,
    SupportsRecovery,
)

from .compress import count_tokens, split_hunk_by_budget, split_patch_into_hunks
from .injection import wrap_diff, wrap_not_shown
from .lenses import _Lens
from .notices import _plural
from .parse import ParseError, parse_findings
from .profiling import profiler
from .redact import redact
from .repair import repair_findings
from .run import (
    _BlocksFor,
    _LensOutcome,
    _OnOversized,
    _PreparedCall,
    _Run,
    _skip_reason,
    _stamp_and_bound,
)
from .stages import files_not_visible

# The engine's logger; see the note in run.py.
_log = get_logger("lgtmaybe.engine.engine")

# When reasoning accounts for at least this share of the `max_tokens` ceiling a
# truncation hit, the ceiling was spent on THINKING, not on findings — and the
# split (see _review_split) cannot help, because a smaller payload does not
# shrink a thinking budget.
#
# 0.9 because the two populations are nowhere near it from either side. Measured
# on one self-review of this repo: five calls truncated at a 32,768 ceiling
# having spent 25,963–35,463 tokens reasoning — 0.79 to over 1.0 of the cap,
# clustering at 0.98+ — while the calls that answered normally wrote thousands of
# tokens of findings after their thinking. The failure mode the split exists for
# is the opposite shape: little thought, then an answer that runs long, where the
# ratio is near zero. At 0.9 at most a tenth of the ceiling was left for the
# answer, so halving the diff cannot buy a complete one — the only lever left is
# `reasoning_effort`, which bounds the thinking directly.
#
# Deliberately not configurable: it is a diagnosis, not a preference, and a knob
# here would be one more thing to tune in the failure it exists to explain.
_REASONING_DOMINANT_SHARE = 0.9


class _RetryableReason(str):
    """A failure reason a later, identical call could still succeed at.

    The provider was briefly unavailable — a capacity 429, a 5xx, a stalled
    connection, a blown wall clock — so the same request issued once the fan-out
    has drained is worth one more go (see :func:`_rescue`). That
    is the opposite of a failure we caused: unparseable output at temperature 0
    returns the same unparseable answer, and a ceiling the user set
    (`max_review_seconds`, `max_review_tokens`, a termination signal) is not a
    fault to retry past at all.

    A plain ``str`` subclass, deliberately: every existing consumer — the
    ``errors`` list, the summary's f-strings, the ``_BUDGET_SKIP_REASON``
    comparison — keeps working byte-for-byte, and the flag rides along beside the
    text instead of a wider outcome tuple threaded through six call sites.
    """

    __slots__ = ()


class _PayloadReason(_RetryableReason):
    """A provider failure that is ALSO evidence the payload was too big.

    A blown wall clock or output ceiling is retryable in the sense the *adapter*
    cares about — a genuinely later request may well succeed — but not in the
    sense the *split* cares about. Once the pieces have been tried, re-sending
    the original whole payload is the one thing already known not to work.

    The distinction exists because :meth:`_Ladder._review_split` has to
    tell two failures apart that both arrive as "the piece call failed":
    ``the smaller payload also ran out of room`` (nothing left to try) and ``a
    piece hit a capacity 429`` (the provider faltered, and the rescue wave is
    exactly what should have it). Collapsing them excluded the second.
    """

    __slots__ = ()


class _PiecesReason(_RetryableReason):
    """A split whose pieces faltered on the provider: the rescue re-runs just those.

    Re-running the call that split would replay the original whole batch — the
    payload :class:`_PayloadReason` already knows cannot work, another full
    timeout or ``max_tokens`` bill — and then every piece again, the ones that
    answered included (observed as whole, a, b, whole, a, b). So the split hands
    the rescue wave the only retry worth paying for: :attr:`retry`, the failed
    pieces alone. A plain attribute, because a ``str`` subtype cannot carry a
    non-empty ``__slots__``.
    """

    retry: Callable[[], _LensOutcome]


def _provider_side(reason: str | None) -> bool:
    """True when a split piece failed on the provider, not on its own size."""
    return _rescuable(reason) and not isinstance(reason, _PayloadReason)


class _ReasoningBoundReason(str):
    """A truncation whose ceiling went on thought, not on the answer.

    Produced only by :func:`_reasoning_exhausted_reason`, and read by
    :meth:`_Ladder._retry_lower_effort` to tell a step-down retry that
    cut the same way (take the off switch) from one that cut for another reason
    (stop). NOT a :class:`_RetryableReason`: an identical request runs to the
    identical ceiling, so the rescue wave has nothing to offer it. The same
    plain-``str`` trick as its siblings, for the same reasons.
    """

    __slots__ = ()


def _rescuable(reason: str | None) -> bool:
    """True when *reason* is a provider-side failure worth one more attempt."""
    return isinstance(reason, _RetryableReason)


# How many rescue calls run at once. The wave exists BECAUSE the backend was
# under pressure — a capacity limit, an overloaded endpoint — so re-bursting the
# full fan-out width into it is the one thing most likely to reproduce the
# failure it is trying to undo. Two is enough to keep a multi-lens rescue from
# running strictly end-to-end.
_RESCUE_WORKERS = 2


def _split_batch(
    batch: list[tuple[str, str]], *, slice_hunks: bool = False
) -> list[list[tuple[str, str]]]:
    """Split a timed-out batch into smaller review units.

    Multi-file: halve the file list — the coarsest cut that halves the payload,
    and the one that keeps each file's patch intact. Single file: fall back to its
    hunks, the same unit the recursive walk uses, so an oversized lone file still
    shrinks. Returns fewer than two pieces when there is nothing smaller to try
    (an empty batch, or one file with a single hunk).

    ``slice_hunks`` lets a lone hunk be cut *inside*, at half its own size, with
    the same slicer the recursive walk uses. Only for a prompt the model's
    context window refused: that failure is purely about input size, so a
    smaller slice is guaranteed to help, where a timeout or a truncation on a
    single hunk may be about the answer or the thinking, and slicing there is
    the speculative recursion this split deliberately refuses.
    """
    if len(batch) > 1:
        middle = len(batch) // 2
        return [batch[:middle], batch[middle:]]
    if not batch:
        return []
    path, patch = batch[0]
    hunks = split_patch_into_hunks(patch)
    if len(hunks) < 2:
        if not slice_hunks or not hunks:
            return []
        slices = split_hunk_by_budget(hunks[0], max(1, count_tokens(hunks[0]) // 2))
        return [[(path, piece)] for piece in slices] if len(slices) >= 2 else []
    middle = len(hunks) // 2
    return [
        [(path, hunk) for hunk in hunks[:middle]],
        [(path, hunk) for hunk in hunks[middle:]],
    ]


def _rescue(calls: list[_PreparedCall], outcomes: list[_LensOutcome]) -> list[_LensOutcome]:
    """Re-run the calls that failed on the provider, once, and merge them in.

    One flaky call used to void the whole round: three consecutive reviews
    each reported "1 of 4 review calls failed" while the other three lenses
    succeeded, and a run fifteen minutes later found what the partial rounds
    had missed. The lenses that answered are not the problem — the one that
    did not is, and the cheapest honest fix is to ask it again.

    Bounded on every axis that matters:

    - **Only provider-side failures** (:class:`_RetryableReason`). Unparseable
      output re-runs to the same unparseable answer at temperature 0, and a
      ceiling the user set is not a fault to retry past.
    - **One wave.** The rescue's own outcome is never rescued again, because
      this runs once — a lens that is genuinely down costs exactly one extra
      call and then reports itself, rather than grinding.
    - **Every ceiling still applies.** Each rescue re-enters ``_review_lens``,
      which re-checks ``_skip_reason`` first, so `max_review_seconds`, the
      token budget and a termination signal all still stop it dead.
    - **A pool of its own, entered only after the fan-out's has closed.** Never
      submitted from inside a fan-out worker: a worker that submits to its own
      pool and blocks on the result deadlocks the moment the pool saturates
      (the same trap ``_review_split`` avoids by construction). And narrow —
      see :data:`_RESCUE_WORKERS`.

    Findings from both attempts are kept and left to the pipeline's ``_dedupe``
    to collapse, exactly as a deferral's re-run is: a wall timeout can salvage
    real findings before it fails, and binning them because the second attempt
    also produced some would lose work the provider already billed for.
    """
    retrying = [i for i, (_, error) in enumerate(outcomes) if _rescuable(error)]
    if not retrying:
        return outcomes
    _log.warning(
        "re-running review calls that failed on the provider",
        extra={"calls": len(retrying), "lenses": [calls[i].lens_id for i in retrying]},
    )
    merged = list(outcomes)

    def rerun(index: int) -> _LensOutcome:
        # A split's failed pieces, never the oversized whole — see _PiecesReason.
        error = outcomes[index][1]
        return error.retry() if isinstance(error, _PiecesReason) else calls[index].task()

    width = min(len(retrying), _RESCUE_WORKERS)
    with ThreadPoolExecutor(max_workers=width, thread_name_prefix="lgtmaybe-rescue") as pool:
        # map() yields in submission order, so the merge stays deterministic
        # however the calls interleave.
        rescued = list(pool.map(rerun, retrying))
    for index, (findings, error) in zip(retrying, rescued, strict=True):
        first_findings, _ = merged[index]
        merged[index] = (first_findings + findings, error)
    return merged


class _Reissue(Protocol):
    """One more call of the same lens, with no split and no deferral of its own.

    The engine's ``_complete_lens`` with both callbacks left out: every rung
    re-runs on that call, and leaving them out is what stops a rung recursing.
    The keyword flags each mark the rung that issued the call:

    ``effort`` carries the per-call reasoning override of a step-down retry
    (see :meth:`_Ladder._retry_lower_effort`). Not None also MARKS this call as
    that retry, which is what bounds it to one: the step-down is only ever
    offered to a call that has not already taken it.

    ``recover`` is the same idea for the unparseable path: False marks a call
    that IS a recovery, so it may not start another. Set only by
    :meth:`_Ladder._retry_without_schema`, which is the second and last level.

    ``escalate_to`` names the fallback model this one call should run on. Set
    only by :meth:`_Ladder._escalate_model`; it reaches the adapter as
    ``model_override``, which also stops the adapter falling back from a call
    that already is the fallback.
    """

    def __call__(
        self,
        messages: list[Message],
        response_format: type[ReviewResult] | None,
        batch_num: int,
        lens: _Lens,
        *,
        effort: dict[str, Any] | None = ...,
        recover: bool = ...,
        escalate_to: str | None = ...,
        run: _Run,
    ) -> _LensOutcome: ...


class _ReviewPiece(Protocol):
    """One split piece's lens call: the engine's ``_review_lens`` without a batch.

    With no batch there is nothing to split again and no deferral to offer, so
    a piece that fails the same way just fails.
    """

    def __call__(
        self,
        run: _Run,
        wrapped: str,
        intent_block: str | None,
        hint_block: str | None,
        dir_block: str | None,
        batch_num: int,
        lens: _Lens,
        *,
        spec_block: str | None = ...,
        hidden_block: str | None = ...,
    ) -> _LensOutcome: ...


@dataclass(frozen=True)
class _Ladder:
    """The recovery steps one lens call may take, and the seam back into the engine.

    ``recovery`` is the adapter's ``SupportsRecovery``, or None when it offers
    none (then every step it would supply is simply never taken). ``reissue``
    and ``review_piece`` call back into the engine; see :class:`_Reissue` and
    :class:`_ReviewPiece`. The engine builds one per lens call, so nothing
    here outlives the call it serves.
    """

    recovery: SupportsRecovery | None
    reissue: _Reissue
    review_piece: _ReviewPiece

    def _remedy_oversized(
        self,
        completed: list[ReviewFinding],
        reason: str,
        exhausted: str | None,
        *,
        messages: list[Message],
        response_format: type[ReviewResult] | None,
        batch_num: int,
        lens: _Lens,
        run: _Run,
        on_oversized: _OnOversized | None,
        effort: dict[str, Any] | None,
        escalate_to: str | None,
        slice_hunks: bool = False,
    ) -> _LensOutcome:
        """Work the truncation ladder for one cut-off lens call.

        The whole remedy in one place, because a cut-off generation reaches the
        engine by two different routes and which one fires is an accident of what
        the provider reports, not a fact about the failure:

        - the adapter sees it, when the route says ``length`` or the response
          spends a ceiling we configured, and raises ``ProviderTruncated``;
        - the PARSER sees it, when the route says nothing useful — litellm
          rewrites an unrecognised finish reason to ``stop``, and a model can stop
          mid-object well short of the cap — and only the unterminated JSON gives
          it away.

        The second route used to end here with a reason saying no split was
        attempted, so an identical failure cost the whole lens on one model and
        merely a slower review on another. Measured on a gateway model at 43.2%
        completeness over 100k-token inputs: what the declared ceiling bought was
        recovery, not accuracy.

        *completed* is the salvage, already stamped. *exhausted* is the
        reasoning-bound diagnosis (see :func:`_reasoning_exhausted_reason`) or
        None, decided by the caller because only it holds the right denominator.

        The rungs, in order, and never more than one of the first two:
        shrink the payload (:meth:`_review_split`) when the answer outgrew the
        ceiling, step the thinking down (:meth:`_retry_lower_effort`) when the
        reasoning did, and only then change the model
        (:meth:`_escalate_model`) — which says nothing about the failure, so it
        goes last and is spent by the whole batch rather than by each piece.
        """
        if exhausted is not None:
            # The ceiling went on thinking, not on findings: shrinking the
            # payload cannot shrink that, so the split is skipped and the
            # reason names the lever that does move it. Checked before the
            # "already a piece" case below so a piece reports it too — it is
            # the better diagnosis wherever the truncation happens.
            _log.warning(
                "truncation was reasoning-bound — not splitting",
                extra={"lens": lens.id, "batch": batch_num},
            )
            if effort is not None or escalate_to is not None:
                # Either this IS the step-down retry and it went the same
                # way — one attempt, not a cascade: the lower level did
                # not fit either, and grinding down the ladder spends the
                # whole review proving it — or this is the escalation,
                # which is the last rung and takes no remedy of its own.
                # Stepping THAT down would re-run the primary at an effort
                # the primary has already failed at. The caller that
                # offered the step-down decides what follows it.
                return completed, exhausted
            retried, retry_reason = self._retry_lower_effort(
                messages, response_format, batch_num, lens, exhausted, run
            )
            if retry_reason is None or on_oversized is None:
                # Answered, or this is a piece/retry — see _escalate_model
                # for why only the whole batch may buy a fallback call.
                return completed + retried, retry_reason
            escalated, escalate_reason = self._escalate_model(
                messages, response_format, batch_num, lens, retry_reason, run
            )
            return completed + retried + escalated, escalate_reason
        if on_oversized is None:
            # Already a piece: nothing smaller to try. Report the reason
            # rather than recurse — an unbounded cascade would spend the
            # whole review on a model that cannot answer at any size.
            return completed, reason
        findings, split_reason = on_oversized(reason, slice_hunks=slice_hunks)
        if split_reason is None:
            return completed + findings, None
        if isinstance(split_reason, _PiecesReason):
            # The split worked — the payload shrank and a piece answered — so the
            # remaining failure is a transient provider blip on one piece, and the
            # reason already carries that piece's retry. Escalating here re-sends
            # the request the split just recovered from and replaces the piece
            # reason with the fallback's own failure, so the rescue wave would
            # replay the oversized whole batch the split exists to avoid.
            return completed + findings, split_reason
        # The pieces did not cover the batch either, so the payload was
        # never the whole story. Last rung: the same request, a different
        # model. Any findings the pieces did manage ride along, and the
        # overlap with the escalation's collapses in `_dedupe`.
        escalated, escalate_reason = self._escalate_model(
            messages, response_format, batch_num, lens, split_reason, run
        )
        return completed + findings + escalated, escalate_reason

    def _review_split(
        self,
        reason: str,
        *,
        run: _Run,
        batch: list[tuple[str, str]],
        intent_block: str | None,
        hint_block: str | None,
        dir_block: str | None,
        batch_num: int,
        lens: _Lens,
        spec_block: str | None = None,
        hidden_block: str | None = None,
        blocks_for: _BlocksFor | None = None,
        slice_hunks: bool = False,
    ) -> _LensOutcome:
        """Re-review an oversized batch as smaller pieces, one call each.

        The only retry a payload-sized failure can benefit from — whether the call
        ran out of *time* (a wall timeout) or ran out of *room to answer* (the
        model's output ceiling). Repeating the same request is pointless — same
        payload, same model, same budget, same ceiling — but the payload is the one
        thing we can change, and a smaller one both fits the budgets better and gets
        fresh ones. Failing the lens outright instead would discard the whole
        batch's review over a size problem, which is what a real run did: three of
        four lenses produced nothing because each had more to say than one response
        could hold.

        Bounded on purpose: exactly ONE level (the pieces are reviewed with
        ``batch=None``, so a piece that fails the same way again just fails), and
        the pieces are re-wrapped from the same redacted patches, so nothing
        unredacted or unwrapped reaches the model. Returns the original *reason*
        unchanged when the batch cannot be split — a single-hunk file has no
        smaller unit to fall back to.

        The pieces run concurrently (see below) and each still goes through
        ``_review_lens``, so each re-checks the whole-review deadline and token
        budget at execution: a split that begins past a ceiling costs nothing,
        and running the pieces together only shortens the overshoot a split in
        flight can add — it was already bounded at one call's latency.

        Not attempted at all when the truncation was reasoning-bound; see
        :func:`_reasoning_exhausted_reason`, which decides that before we get here.
        """
        pieces = _split_batch(batch, slice_hunks=slice_hunks)
        if len(pieces) < 2:
            # Nothing smaller to try, so nothing has retried this call at all —
            # the reason travels on unchanged, retryable flag and all, and a
            # transient stall still gets its one go from the rescue wave.
            return [], reason
        _log.warning(
            "review call was too big for one response — retrying on smaller pieces",
            extra={"lens": lens.id, "batch": batch_num, "pieces": len(pieces)},
        )

        def review_piece(piece: list[tuple[str, str]]) -> _LensOutcome:
            # Recomputed, never inherited: this piece carries a subset of the
            # batch's files, so the batch's manifest would omit exactly the
            # sibling-piece files this call is now missing — and so would its
            # intent, spec, hint and directory blocks, which additionally
            # carry hints and rules for files the piece cannot see.
            piece_paths = {path for path, _ in piece}
            piece_hidden = wrap_not_shown(files_not_visible(run.changed_files, piece_paths))
            piece_intent, piece_hint, piece_dir, piece_spec = (
                blocks_for(piece_paths)
                if blocks_for is not None
                else (intent_block, hint_block, dir_block, spec_block)
            )
            return self.review_piece(
                run,
                wrap_diff("\n".join(patch for _, patch in piece)),
                piece_intent,
                piece_hint,
                piece_dir,
                batch_num,
                lens,
                spec_block=piece_spec,
                hidden_block=piece_hidden,
            )

        # The pieces are independent calls, so they run CONCURRENTLY — serially
        # a split cost one full model latency per piece, and it does so from
        # inside a fan-out worker that is already holding a slot, which on a run
        # where most lenses split is the largest wall-clock multiplier there is.
        #
        # In a POOL OF ITS OWN, deliberately, never back into the global fan-out
        # pool: this code runs on one of that pool's workers, and a worker that
        # submits to its own pool and then blocks on the result deadlocks the
        # moment the pool is saturated — every worker waiting on work that only a
        # free worker could start. A private executor makes that impossible by
        # construction rather than by argument. It costs at most `width` threads
        # for the duration of one split, and a split is the exceptional path.
        #
        # Width is the review's own concurrency (never the piece count), so a
        # user who has told us what their backend can take — `max_concurrency`,
        # which on a local server is how they describe its parallelism — gets
        # that answer in both pools rather than one setting meaning two things.
        # At width 1 the executor runs them in submission order, exactly as the
        # serial loop did. A splitting worker is blocked, not calling, so the
        # calls in flight peak at the fan-out's width times this one — the
        # adapter's backoff absorbs that burst, and it only happens on the run
        # where every lens is already failing.
        findings: list[ReviewFinding] = []
        errors: list[str] = []
        width = min(len(pieces), run.concurrency)
        with ThreadPoolExecutor(max_workers=width, thread_name_prefix="lgtmaybe-split") as pool:
            # map() yields in submission order, so the pieces' findings and the
            # reported error stay deterministic however the calls interleave.
            outcomes = list(pool.map(review_piece, pieces))
        for piece_findings, piece_error in outcomes:
            findings.extend(piece_findings)
            if piece_error is not None:
                errors.append(piece_error)
        if len(errors) < len(pieces):
            # At least one piece answered, so the shrink did produce a review —
            # only then is "reviewed in smaller pieces" a true claim.
            run.state.split_batches.add(batch_num)
        # ANY failed piece is still a failed call. Swallowing it because a sibling
        # succeeded would drop the incomplete-results notice while the summary
        # claimed the batch was reviewed — a clean bill of health for code no model
        # ever saw. The last error is reported (the split's own failure, not the
        # original timeout) so the notice names what actually went wrong.
        #
        # Whether the rescue wave may have this one turns on WHY the piece failed,
        # and the two answers are opposite:
        #
        # - A piece that ran out of room again (:class:`_PayloadReason`) is
        #   reported plain. The split is already this call's retry, and it retried
        #   with the one change that can help — a smaller payload. Re-sending the
        #   original oversized request is the "identical request against an
        #   identical budget" the adapter refuses to repeat, at twice the calls.
        # - A piece that failed on the PROVIDER — a capacity 429, a 5xx, a stalled
        #   connection — says nothing about size. One more go after the wave has
        #   drained is exactly what it needs — for THAT piece alone, which is
        #   what :class:`_PiecesReason` hands the rescue wave.
        #
        # Collapsing the two is the bug this replaces: every split failure was
        # reported plain, so a transient blip inside a piece silently forfeited
        # the rescue the same blip would have got anywhere else.
        if not errors:
            return findings, None
        # The message and the retryability come from different places, and reading
        # both off the last error conflates them: a piece that failed on the
        # provider followed by one that ran out of room reports a payload reason
        # last, and the provider failure — the case this exists to rescue — would
        # lose its turn behind it. So the MESSAGE is the last error (the split's
        # own failure, which is what the notice should name), while retryability
        # is a property of ANY piece having faltered provider-side.
        last = errors[-1]
        flaky = [piece for piece, (_, e) in zip(pieces, outcomes, strict=True) if _provider_side(e)]
        if not flaky:
            return findings, str(last)
        settled = [e for e in errors if not _provider_side(e)]

        def retry() -> _LensOutcome:
            # Serial: this runs on a rescue worker, and that wave is narrow on
            # purpose (see _RESCUE_WORKERS). Plain errors out — the rescue is
            # one wave, so its outcome is never rescued again.
            again = [review_piece(piece) for piece in flaky]
            if any(error is None for _, error in again):
                run.state.split_batches.add(batch_num)
            remaining = settled + [error for _, error in again if error is not None]
            return [f for found, _ in again for f in found], (
                str(remaining[-1]) if remaining else None
            )

        reason = _PiecesReason(last)
        reason.retry = retry
        return findings, reason

    def _retry_lower_effort(
        self,
        messages: list[Message],
        response_format: type[ReviewResult] | None,
        batch_num: int,
        lens: _Lens,
        reason: str,
        run: _Run,
    ) -> _LensOutcome:
        """Re-run one lens once with its reasoning effort stepped down a level.

        The missing sibling of :meth:`_review_split`. Both start from the same
        failure — the answer hit the output ceiling — and the diagnosis decides
        which remedy applies: a payload-bound truncation shrinks the payload, a
        reasoning-bound one cannot (halving the diff does not halve the thinking),
        so it changes the only variable that does move a thinking budget.

        Without this, the reasoning branch diagnosed correctly and then stopped,
        losing the lens for that round — a real dogfood review reviewed its tests
        and documentation with nothing at all, and the message it left named a
        knob for the reader to turn by hand next time.

        Bounded exactly like the split:

        - **One attempt.** The retry runs with ``effort`` set, and that is the
          same flag :meth:`_remedy_oversized` checks before offering a step-down —
          so a retry that truncates the same way reports plain.
        - **Down, never up**, one level, and only where
          :func:`_reasoning_exhausted_reason` already fired.
        - **No split, no deferral** on the retry (both callbacks are None): the
          payload was never the problem.
        - **Nothing when the effort is unset.** The adapter answers None for the
          library default, so a user who never configured it pays nothing and
          sends byte-identical requests.
        - **Every whole-review ceiling still applies.** ``_skip_reason`` is
          re-checked before the call, exactly as ``_review_split``'s pieces do by
          re-entering ``_review_lens`` — this path reaches ``_complete_lens``
          directly, so it has to make that check itself. A retry that would begin
          past the deadline, the token budget or a termination signal reports the
          truncation it already had instead of spending past a stop.

        Returns the retry's ``(findings, error)``. The cut call's salvage is
        merged by the caller, exactly as the split's is.
        """
        # An optional capability, like `post_issue_comment` on the GitHub side:
        # the effort lives in provider-shaped opts (a flat `reasoning_effort`,
        # or OpenRouter's nested `reasoning` object), and the engine has no
        # business knowing which. A provider without it simply never steps down.
        if _skip_reason(run, lens) is not None:
            # Past a ceiling: the run is already stopping, and the notice it will
            # carry is the truncation's, not a skip's — this call never happened.
            return [], reason
        recovery = self.recovery
        step_down = recovery.lower_reasoning_effort() if recovery is not None else None
        if not step_down:
            return [], reason
        _log.warning(
            "retrying the lens once at a lower reasoning effort",
            extra={"lens": lens.id, "batch": batch_num, "effort": step_down},
        )
        findings, error = self.reissue(
            messages, response_format, batch_num, lens, effort=step_down, run=run
        )
        if error is None:
            # Recorded on the way OUT, not the way in: the notice claims findings
            # came from the lower setting, and a retry that truncated again
            # produced no such findings. That run reports the failure it already
            # had — claiming a recovery on top of it would be two wrong notices.
            run.state.stepped_down.add(lens.id)
            return findings, error
        if not isinstance(error, _ReasoningBoundReason):
            # The lower effort cut for some OTHER reason — the answer outgrew the
            # ceiling, a quota, a refused request. Thinking less again is not the
            # remedy for any of those, and the caller decides what is.
            return findings, error
        # The graded step landed nowhere: the same ceiling, spent the same way.
        # Measured on GLM 5.3 through OpenRouter (a large PR, three rounds), where
        # `low` and `minimal` both spent the whole 12,288 ceiling on thought in
        # 10 retries of 11, and the fallback model then did the same at ten times
        # the price. A model whose thinking is a switch does not answer to a
        # dial, so the one further step is to off — and it is taken BEFORE the
        # model changes, because switching model says nothing about the failure.
        switch_off = recovery.disable_reasoning() if recovery is not None else None
        if not switch_off or switch_off == step_down:
            # No switch on this route, or the step-down already was it (a
            # configured `minimal` steps to `none`): a second request at the
            # same setting is the byte-identical call that just failed.
            return findings, error
        if _skip_reason(run, lens) is not None:
            return findings, error
        _log.warning(
            "the lower effort exhausted the ceiling too — retrying once with reasoning off",
            extra={"lens": lens.id, "batch": batch_num, "effort": switch_off},
        )
        more, error = self.reissue(
            messages,
            response_format,
            batch_num,
            lens,
            effort=switch_off,
            run=run,
        )
        if error is None:
            run.state.reasoning_off.add(lens.id)
        return findings + more, error

    def _escalate_model(
        self,
        messages: list[Message],
        response_format: type[ReviewResult] | None,
        batch_num: int,
        lens: _Lens,
        reason: str,
        run: _Run,
    ) -> _LensOutcome:
        """Re-run one lens once on the configured fallback model.

        The last rung of the truncation ladder, and the only one that changes the
        MODEL. Its siblings — :meth:`_review_split` for a payload-bound
        truncation, :meth:`_retry_lower_effort` for a reasoning-bound one — both
        act on what the token counts actually said went wrong, and both stay on
        the model the user chose. This one says nothing about the failure at all:
        it re-sends the same request at the same ceiling and hopes a second model
        finishes it.

        So it goes LAST. The adapter used to take it first — its own fallback
        fired the moment the primary truncated, which meant the two aimed
        remedies never ran and every truncation cost a second model's full
        ceiling. Lens calls now carry ``defer_truncation``, which hands the
        failure back here instead (see ``LiteLLMProvider.complete``).

        Bounded exactly like the retries beside it:

        - **One attempt, and no ladder of its own.** The call goes out with
          ``on_oversized``/``on_needs`` None — the same condition that stops a
          split piece recursing — and with the step-down and the unparseable
          recoveries off. Every one of those re-runs would go out on the PRIMARY,
          which is the model that just failed this lens.
        - **The batch's to spend, not a piece's.** Only a call still holding an
          ``on_oversized`` reaches here, so a split whose pieces all failed buys
          ONE fallback call for the batch rather than one per piece.
        - **Nothing when no fallback is configured.** ``escalate_model`` answering
          None returns the reason unchanged, so a run without a second model
          sends byte-identical requests and pays exactly what it paid before.
        - **Every whole-review ceiling still applies.** ``_skip_reason`` is
          re-checked first: a run already past its deadline, token budget or a
          termination signal reports the truncation it has rather than spending
          past a stop.

        Nothing is recorded here. The disclosure is read off the answering model
        on the result (:meth:`_note_answering_model`), which also catches the
        adapter switching model on its own for a failure that never reaches this.
        """
        if _skip_reason(run, lens) is not None:
            return [], reason
        # A recovery step, like `lower_reasoning_effort`: which litellm model
        # string a configured fallback resolves to is the adapter's knowledge,
        # and a provider without a second model answers None.
        recovery = self.recovery
        target = recovery.escalate_model() if recovery is not None else None
        if not target:
            return [], reason
        _log.warning(
            "re-running the lens once on the fallback model",
            extra={"lens": lens.id, "batch": batch_num, "fallback_model": target},
        )
        return self.reissue(
            messages,
            response_format,
            batch_num,
            lens,
            escalate_to=target,
            # No unparseable recovery either: both re-runs it could start
            # (`repair_findings`, `_retry_without_schema`) go out on the primary,
            # which is the model that just failed this lens.
            recover=False,
            run=run,
        )

    def _note_answering_model(self, result: ProviderResult, lens: _Lens, run: _Run) -> None:
        """Record that a model other than the primary produced this result.

        Read off the result rather than stamped at the call site because the
        engine is not the only thing that switches model: the adapter still falls
        back on its own for every failure that is not a truncation, and from up
        here that call is indistinguishable from an ordinary success. Recording
        only the escalations the engine drove would disclose half the rescues and
        leave the other half silent — which is the state this replaces.

        Fail-closed on both sides: an adapter or fake that reports neither model
        notes nothing, because "a second model answered" is a claim, and one that
        cannot be proved should not be made.
        """
        answered = result.model
        # The adapter's resolved primary, not `run.model`: the result names the
        # litellm model string, which the raw configured name never equals.
        recovery = self.recovery
        asked = recovery.model if recovery is not None else None
        if answered and asked and answered != asked:
            run.state.escalated[lens.id] = answered

    def _retry_without_schema(
        self,
        messages: list[Message],
        model: str,
        batch_num: int,
        lens: _Lens,
        reason: str,
        run: _Run,
    ) -> _LensOutcome:
        """Re-run one lens once with provider-native schema enforcement off.

        The third structured-output fallback, and the only one the adapter could
        not have found for itself. It already handles a provider that *rejects*
        ``response_format`` (a 400 naming the param) and one whose schema mode
        decodes to an *empty* string. A reply that arrives non-empty, well-formed
        on the wire, and simply is not findings looks like a clean success from
        down there — only the parser knows better.

        The theory this acts on: the prompt asks for the findings JSON regardless
        of the schema, and the parser is lenient about fences, prose wrappers and
        trailing commas, so a model whose constrained decoding produced something
        unusable may well answer perfectly without it. Two Claude models did
        exactly this through OpenRouter, losing every lens in an observation.

        Bounded like the step-down beside it:

        - **One attempt.** ``recover=False`` marks the re-run, and that is the
          same flag the unparseable branch checks before offering a recovery, so
          a re-run that fails reports plain rather than reformatting again.
        - **Only after the cheap salvage.** The reformat call sends no diff and
          costs a fraction of this; ordering by cost is deliberate.
        - **Only where a schema was actually sent.** With ``structured_output``
          off there is no enforcement to blame and the re-run would be the
          byte-identical request the rescue wave forbids.
        - **No split, no deferral** (both callbacks None): the payload was never
          the problem.
        - **Every whole-review ceiling still applies** — ``_skip_reason`` is
          re-checked here because the reformat before it already spent a call.

        A re-run that *works* is evidence, not noise: the model is told to stop
        sending the schema, so later batches skip the two wasted calls instead of
        rediscovering this. A re-run that fails proves nothing — one bad reply is
        not a broken schema mode, and disabling it for the rest of the run would
        be a quality regression on every later call — so nothing is remembered.
        """
        if _skip_reason(run, lens) is not None:
            return [], reason
        _log.warning(
            "re-asking the lens without the provider's JSON schema",
            extra={"lens": lens.id, "batch": batch_num},
        )
        findings, error = self.reissue(messages, None, batch_num, lens, recover=False, run=run)
        if error is None:
            # Recorded on the way OUT, like the step-down: a re-run that failed
            # too produced no findings to claim, and marking the model off the
            # back of it would disable structured output on a guess.
            run.state.re_asked.add(lens.id)
            self._drop_response_format(model)
        return findings, error

    def _sends_response_format(self, model: str) -> bool:
        """Whether the schema this call asked for actually reached the provider.

        Passing ``response_format`` is not the same as sending it: the adapter
        strips it for a model that has already refused it (a 400, or schema mode
        decoding to nothing), and from here that call looks identical to one
        made under enforcement. Re-running such a lens "without the schema"
        would re-send the request that just failed, byte for byte — a wasted
        full-diff call, and exactly what ``engine.rescue`` forbids.

        Probed through ``SupportsRecovery`` and **fail-open**, like every other
        adapter probe: an adapter that cannot answer is assumed to have sent what
        it was given, so the recovery keeps working rather than silently
        switching itself off.
        """
        recovery = self.recovery
        if recovery is None:
            return True
        return bool(recovery.sends_response_format(model))

    def _drop_response_format(self, model: str) -> None:
        """Tell the adapter this model's schema mode is not working.

        Probed through ``SupportsRecovery``, and the one recovery step that
        WRITES. It stays off the frozen ``ProviderClient`` port for the same
        reason the read-only ones do: the knowledge is adapter-shaped (which
        litellm model string keys the drop), and a provider that cannot honour it
        should simply not remember rather than every fake in the suite growing a
        no-op.
        """
        recovery = self.recovery
        if recovery is not None:
            recovery.drop_response_format(model, "unparseable-output")

    def _call_failed(
        self,
        exc: Exception,
        started: float,
        *,
        messages: list[Message],
        response_format: type[ReviewResult] | None,
        batch_num: int,
        lens: _Lens,
        run: _Run,
        on_oversized: _OnOversized | None,
        effort: dict[str, Any] | None,
        escalate_to: str | None,
    ) -> _LensOutcome:
        """A lens call whose provider call raised: name it, record it, remedy it.

        *started* is the ``time.perf_counter()`` instant the call went out. The
        payload-sized failures (a wall timeout, a truncation, a refused prompt)
        work the truncation ladder; every other failure reports its reason,
        marked retryable when the rescue wave may have it.
        """
        # Retryable by default: this is the provider faltering, not us, so the
        # rescue wave gets one more go once the fan-out has drained. Two
        # exceptions, both because the second call is guaranteed to buy the
        # same answer at full price:
        #
        # - a truncation is a blown output CEILING, and the identical request
        #   runs to the identical ceiling;
        # - a failure the adapter stamped UNRECOVERABLE — a dead key, a spent
        #   quota, a refused request — cannot resolve itself mid-review. The
        #   adapter already made that judgement to decide its own retries;
        #   re-deriving it here from the message text would be a second copy
        #   of the rule, drifting from the first.
        reason: str = _error_reason(exc)
        if not isinstance(exc, ProviderTruncated | ProviderInputTooLarge) and not is_unrecoverable(
            exc
        ):
            # A wall timeout is retryable, but it is also the signal the split
            # acts on, so it is marked as such — see _PayloadReason.
            mark = _PayloadReason if isinstance(exc, ProviderWallTimeout) else _RetryableReason
            reason = mark(reason)
        profiler.record_error(lens.id, batch_num, time.perf_counter() - started, exc, reason)
        _log.warning(
            "review call failed",
            extra={"lens": lens.id, "reason": reason},
            exc_info=True,
        )
        if isinstance(exc, ProviderWallTimeout | ProviderTruncated | ProviderInputTooLarge):
            # Three payload-sized failures, one remedy: ran out of time, ran
            # out of room to answer, ran out of room to ask. A refused prompt
            # (the model's context window) has no partial answer and no
            # reasoning breakdown, so the salvage and the exhaustion check
            # below are no-ops for it and it goes straight to the split.
            #
            # Whatever the model finished before the ceiling cut it off is real,
            # schema-valid work — kept, exactly as the parse path keeps it, so
            # the exception path is not the one place a partial answer is binned.
            #
            # The counts come off the exception only when it is a truncation:
            # a call that spent its ceiling HAS no ceiling left, so its
            # `output_tokens` is the denominator. The other two carry neither.
            reasoning, spent = (
                (exc.reasoning_tokens, exc.output_tokens)
                if isinstance(exc, ProviderTruncated)
                else (None, None)
            )
            return self._remedy_oversized(
                _stamp_and_bound(_salvage_truncated(exc, lens), lens, run),
                reason,
                _reasoning_exhausted_reason(reasoning, spent),
                messages=messages,
                response_format=response_format,
                batch_num=batch_num,
                lens=lens,
                run=run,
                on_oversized=on_oversized,
                effort=effort,
                escalate_to=escalate_to,
                # A refused prompt may cut inside a lone hunk (see
                # _split_batch): input size is the whole of that failure, so a
                # smaller slice is guaranteed to help where for the other two
                # it is a guess.
                slice_hunks=isinstance(exc, ProviderInputTooLarge),
            )
        return [], reason

    def _unparseable(
        self,
        exc: ParseError,
        result: ProviderResult,
        elapsed: float,
        *,
        messages: list[Message],
        response_format: type[ReviewResult] | None,
        batch_num: int,
        lens: _Lens,
        run: _Run,
        recover: bool,
    ) -> _LensOutcome:
        """A reply that arrived whole but did not parse: the two recoveries, cheapest first.

        ``recover`` False marks a call that already IS a recovery (see
        :meth:`_retry_without_schema`), so it reports plain.
        """
        reason = _unparseable_reason(
            exc, lens.id, result.text, schema_mode=response_format is not None
        )
        profiler.record_result(lens.id, batch_num, elapsed, result, error=reason)
        # Two recoveries, cheapest first, and `recover` gates BOTH: the
        # schema-less re-run below re-enters here with it False, so a
        # failing retry reports plain instead of starting its own
        # reformat. One flag, because both are the same judgement —
        # "may this failed call spend another one?".
        #
        # Ceilings are re-checked first: these are new model calls, and a
        # run already past its deadline or budget must not start one.
        if recover and _skip_reason(run, lens) is None:
            # 1. One reformat attempt at the answer the model already
            #    produced and was already billed for. A DIFFERENT request
            #    — the reply plus the schema, no diff — which is why it
            #    does not fall under the rule that an unparseable call is
            #    never re-run: that rule is about re-issuing the identical
            #    request at temperature 0.
            repaired = repair_findings(run.provider, run.cfg, result.text, exc.shape, lens.id)
            if repaired is not None:
                # Complete, not partial: nothing is missing, so this must
                # not trip the incomplete notice. Reported through its own
                # notice instead, like a lens that stepped its reasoning
                # effort down — successful, but worth saying out loud.
                run.state.repaired.add(lens.id)
                return _stamp_and_bound(repaired, lens, run), None
            # 2. The reformat could not do it either. If this call sent
            #    the provider's schema, that enforcement is the remaining
            #    suspect — re-run the lens once without it.
            if (
                response_format is not None
                and run.cfg.retry_without_schema
                and self._sends_response_format(run.model)
            ):
                retried, retry_error = self._retry_without_schema(
                    messages, run.model, batch_num, lens, reason, run
                )
                if retry_error is None:
                    return retried, None
                return retried, reason
        return [], reason

    def _cut_off(
        self,
        exc: ParseError,
        result: ProviderResult,
        elapsed: float,
        *,
        messages: list[Message],
        response_format: type[ReviewResult] | None,
        batch_num: int,
        lens: _Lens,
        run: _Run,
        on_oversized: _OnOversized | None,
        effort: dict[str, Any] | None,
        escalate_to: str | None,
    ) -> _LensOutcome:
        """A reply whose body stopped mid-answer though the route called it finished.

        The parser's own truncation detector, and the same ladder as the
        adapter's: see :meth:`_remedy_oversized`.
        """
        # A response cut off at the output ceiling is not a badly-behaved
        # model, and saying "unparseable" sends the reader looking for a
        # prompt bug instead of the ceiling they hit. The notice on the PR is
        # the only place this is ever seen, so it names which fault it was —
        # and how much of the lens survived, because "3 findings recovered"
        # and "0 recovered" are very different states to be told about.
        salvaged = len(exc.recovered)
        recovered_note = (
            f"; {salvaged} finding{_plural(salvaged)} completed before the cut "
            f"{_plural(salvaged, 'is', 'are')} included"
            if salvaged
            else ""
        )
        # Deliberately NOT worded as a `max_tokens` hit. The adapter's error
        # says that because it has proof — a `length` finish reason, or a
        # response that spent a ceiling we set. Here there is none: the route
        # called this a clean finish and the body simply stops, which can mean
        # a cap the route never mentions or a generation that fell over. The
        # old wording asserted the ceiling anyway and printed `output_tokens`
        # as if it were that ceiling, sending the reader to raise a number
        # that may not be the one they hit.
        reason = (
            f"response truncated: the body stopped mid-answer after "
            f"{result.output_tokens} output tokens while the route reported a clean "
            "finish, so the cut was detected from the unterminated body; the batch is "
            f"re-reviewed in smaller pieces automatically{recovered_note}"
        )
        _log.warning(reason, extra={"lens": lens.id, "recovered": salvaged})
        profiler.record_result(
            lens.id,
            batch_num,
            elapsed,
            result,
            findings=salvaged,
            error=reason,
        )
        # Same failure as the adapter-detected one, so the same ladder — this
        # is the wire that was missing. The salvage is stamped like any other
        # finding and rides along; the reason travels with it and is cleared
        # only if a rung actually covers the batch, so a half-answer is never
        # read as a clean bill of health.
        #
        # `slice_hunks` stays False: this is a cut on the way OUT, and unlike
        # a refused prompt it gives no reason to believe a lone hunk is the
        # unit that must shrink.
        return self._remedy_oversized(
            _stamp_and_bound(exc.recovered, lens, run),
            reason,
            # The share that decides whether covering less can help, read off
            # the ceiling the request CARRIED — `output_tokens` here is
            # wherever the body happened to stop, which is a share of nothing.
            _reasoning_exhausted_reason(result.reasoning_tokens, result.output_ceiling),
            messages=messages,
            response_format=response_format,
            batch_num=batch_num,
            lens=lens,
            run=run,
            on_oversized=on_oversized,
            effort=effort,
            escalate_to=escalate_to,
        )


def _reasoning_exhausted_reason(reasoning: int | None, ceiling: int | None) -> str | None:
    """Why splitting this failure cannot help, or None when it still can.

    A truncation normally means the payload asked for more answer than one
    response could hold, and the remedy is to cover less. Not when the ceiling
    went on *thought*: a reasoning model draws its thinking from the same
    `max_tokens` budget, and halving the diff does not halve the thinking. The
    observed proof is a fifteen-line diff truncating at the same ceiling as a
    thousand-line one. Splitting there re-spends the whole ceiling on every piece
    and fails identically — pure added latency on a review that is already slow.

    Decided from the numbers the adapter measured, never from its message: this
    reads the diagnosis as data, exactly as the structured findings contract
    requires. Both counts are needed — the reasoning spend is meaningless without
    the ceiling it is a share of — so a route that reports neither keeps the split
    it has always had.

    Takes the two counts rather than the failure that carried them, because the
    same judgement is now made on two paths and they hold them differently. A
    ``ProviderTruncated`` spent its ceiling by definition, so its `output_tokens`
    IS the denominator; a response the route called finished stopped somewhere
    short of one, so its denominator is the `output_ceiling` the request was sent
    with. Reading `output_tokens` on that second path would compare the thinking
    against however much the model happened to write, which is not a share of
    anything. Callers that hold no counts at all — a wall timeout, a refused
    prompt — pass None and keep their split.

    The returned reason replaces the adapter's, which promises a split this
    failure is not going to get and warns that a higher ceiling makes things
    worse — true of the content runaway it was written for, and the wrong half
    of the advice here. Both levers are named instead, because these numbers
    cannot say which one is the fix.
    Thinking that *expands to fill* whatever ceiling it is given is immune to a
    bigger cap — the case recorded above, where the effort is the only move.
    Thinking with a bounded natural size that merely exceeds this ceiling looks
    identical here and is the opposite case: the cap fixes it outright, and
    lowering the effort pays for the fix in review quality instead. Only a
    re-run at a higher cap separates them, so the reader is handed the choice
    rather than one of the two asserted as proven.
    """
    if not reasoning or not ceiling:
        return None
    if reasoning < ceiling * _REASONING_DOMINANT_SHARE:
        return None
    # The literal name, not `type(exc).__name__`: every reason the engine reports
    # reads as "<Fault>: <what happened>", and this diagnosis is now reached both
    # from a response the adapter raised on and from one only the parser caught.
    # The second has no exception to take a name from, and the fault it describes
    # is the same one either way.
    return _ReasoningBoundReason(
        f"ProviderTruncated: spent {reasoning} of the {ceiling}-token `max_tokens` "
        "ceiling on reasoning, so the batch was not split: a smaller payload cannot "
        "shrink a thinking budget. Lower `reasoning_effort`, or raise `max_tokens` if "
        "this model simply thinks bigger than the ceiling"
    )


def _salvage_truncated(exc: BaseException, lens: _Lens) -> list[ReviewFinding]:
    """Findings the model completed before the output ceiling cut it off.

    The adapter raises the ceiling as its own failure and carries the cut-off body
    with it, so the salvage the parser already performs on a truncation it detects
    itself is available here too. Anything else (a wall timeout has no body at all)
    salvages nothing, and an unparseable remnant is simply empty — never a guess.
    """
    if not isinstance(exc, ProviderTruncated) or not exc.text:
        return []
    try:
        findings = parse_findings(exc.text)
    except ParseError as parse_exc:
        findings = parse_exc.recovered
    if findings:
        _log.info("salvaged findings from truncated response", extra={"lens": lens.id})
    return findings


# How much of an unparseable reply is logged at DEBUG. Head *and* tail: a prose
# preamble shows at the top and an unclosed container or a stray fence at the
# bottom, and the two are different diagnoses. The observed failures ran
# 676–1,201 tokens (roughly 2.7–4.8 KB), so this captures most of them whole —
# and `response_chars` always reports the true length, so a cap never hides how
# much was left out.
_RAW_HEAD_CHARS = 2000
_RAW_TAIL_CHARS = 500


def _raw_excerpt(text: str) -> str:
    """A capped, redacted excerpt of a model reply, safe to put in a log.

    Redacted BEFORE slicing: cutting first could split a PEM block across the
    elision and leave each half unmatched by the redactor. ``core.logging``
    only substitutes secrets explicitly registered with it, so the content
    redactor has to run here or a key the model quoted back at us reaches the
    log untouched.
    """
    clean = redact(text)
    if len(clean) <= _RAW_HEAD_CHARS + _RAW_TAIL_CHARS:
        return clean
    elided = len(clean) - _RAW_HEAD_CHARS - _RAW_TAIL_CHARS
    return f"{clean[:_RAW_HEAD_CHARS]}\n…[{elided} chars elided]…\n{clean[-_RAW_TAIL_CHARS:]}"


def _response_digest(text: str) -> str:
    """A short, content-free identifier for a model reply.

    Two failures are only comparable if you can tell whether they are the same
    failure, and the benchmark evidence behind this could not: it recorded that
    a case failed in all three repeats without being able to say whether the
    three replies were identical. A digest answers that across runs, machines
    and log aggregators while carrying nothing of the reply — which may quote
    the diff back, so it can never go in a default-level log.

    Truncated because it is an identifier, not a checksum: 12 hex characters is
    48 bits, far past any collision a review will produce, and short enough to
    read off a log line.
    """
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def _unparseable_reason(exc: ParseError, lens_id: str, text: str, *, schema_mode: bool) -> str:
    """Report a reply that could not be parsed, and return the reason to post.

    "Unparseable" alone sends a maintainer nowhere — a model answering in prose
    and one emitting broken JSON need different fixes — so the shape rides in
    the returned reason, which is already carried to ``CallRecord.error``, the
    ``--profile`` row and the PR notice. That covers all three without a new
    field on any of them.

    The body itself is model output and can echo the diff, so the default level
    gets its size and its shape and nothing of its content. The body goes out
    only at DEBUG, which is the opt-in this repo already has for exactly this;
    a dedicated flag would be four files of plumbing to say what
    ``LGTMAYBE_LOG_LEVEL`` already says.
    """
    reason = f"unparseable model output ({exc.shape})"
    extra: dict[str, Any] = {
        "lens": lens_id,
        "shape": str(exc.shape),
        "response_chars": len(text),
        # Identity without content: is this the same reply the last run failed on?
        "response_sha256": _response_digest(text),
        # Was provider-native schema enforcement active? Without it, "the model
        # ignored the schema" and "there was no schema" read identically in a
        # log, and they call for opposite fixes.
        "schema_mode": schema_mode,
    }
    if _log.isEnabledFor(logging.DEBUG) and text:
        extra["response_head"] = _raw_excerpt(text)
    _log.warning(reason, extra=extra)
    return reason


def _error_reason(exc: BaseException) -> str:
    """A concise, single-line reason for a failed review call, safe to show inline.

    Leads with the exception type (litellm names are informative — RateLimitError,
    AuthenticationError, Timeout) and collapses the message to one line, capped so
    a verbose provider error doesn't bloat the PR comment.
    """
    text = " ".join(str(exc).split())
    reason = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    return reason[:200]
