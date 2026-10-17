"""LLMReviewEngine: the full review pipeline.

Pipeline: redact → compress/batch → (per batch) fan out one call per review
         lens (concurrent for cloud, serial for ollama) → parse → merge/dedupe
         → require defect evidence → self-reflect/filter → filter by min_severity
         → return findings + summary.

This module orchestrates. The stages it runs live beside it: the preparation
stages in ``stages.py``, the lens set in ``lenses.py``, the per-run types and
ceilings in ``run.py``, the recovery ladder in ``recovery.py``, and the summary
notices in ``notices.py``.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from lgtmaybe.core.diffparse import changed_line_index
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    SCAN_CATEGORY_PREFIX,
    PRContext,
    ReviewCategory,
    ReviewConfig,
    ReviewFinding,
    ReviewResult,
    RiskAssessment,
)
from lgtmaybe.core.ports import Message, ProviderClient, SupportsRecovery

from .astgrep import SymbolResolver
from .compress import count_tokens
from .conflicts import resolve_conflicts
from .injection import wrap_context, wrap_diff, wrap_not_shown, wrap_stack
from .lenses import _build_lenses, _Lens
from .notices import (
    _SCHEMA_DROP_NOTE,
    INCOMPLETE_MARKER,
    LENSES_MARKER_PREFIX,
    _build_notices,
    _compose_summary,
    _NoticeState,
    _summary_line,
)
from .parse import ParseError, parse_findings, parse_needs
from .profiling import profiler
from .prompt import CODE_HEALTH_GROUP, build_shared_preamble, project_context
from .recovery import _Ladder, _rescue
from .reflect import reflect_findings
from .retrieve import MAX_FETCH_FILES, FileFetcher, resolve_needs
from .risk import assess_risk
from .rules import apply_finding_rules
from .run import (
    _BlocksFor,
    _LensOutcome,
    _MeteredProvider,
    _OnOversized,
    _PreparedCall,
    _Run,
    _RunState,
    _skip_reason,
    _stamp_and_bound,
    clear_interrupt,
    interrupt_requested,
    request_interrupt,
)
from .stages import (
    UnbatchedHints,
    _redacted_stack,
    batch_patches,
    blocks_builder,
    cap_files,
    carry_unbatched_hints,
    directory_context,
    expand_context,
    files_not_visible,
    filter_files,
    lens_ceilings,
    passes_path_filters,
    redact_inputs,
    retrieval_budget,
    scan_files,
    spec_context,
    triage_patches,
)
from .static_analysis import MAX_SCAN_FINDINGS, UNANCHORABLE_SCAN_CATEGORIES, ToolFinding
from .suppress import apply_suppressions

# The public surface, including the names that moved out of this module when
# the engine was split and are still imported from here.
__all__ = [
    "INCOMPLETE_MARKER",
    "LENSES_MARKER_PREFIX",
    "LLMReviewEngine",
    "ReviewIncompleteError",
    "clear_interrupt",
    "concurrency_cap",
    "files_not_visible",
    "interrupt_requested",
    "passes_path_filters",
    "request_interrupt",
]

_log = get_logger(__name__)


# Auto concurrency (cfg.limits.max_concurrency=None) — one number, every provider:
#
# - Six everywhere. An extra worker can cut a full-latency wave off the
#   wall clock (only when it changes how many waves there are — wall time is
#   ceil(batches × lenses / workers)), so the pull is upward — but the fan-out
#   is one API key, and the
#   gateways that meter a key meter it per minute, so past some width the burst
#   rate-limits ITSELF. It did: eight concurrent calls against one OpenRouter key
#   produced three consecutive reviews reporting "1 of 4 review calls failed" on
#   a 429. The adapter's backoff (now long enough to outlast a rate window) and
#   the rescue wave both make that survivable rather than fatal; six is the same
#   fix from the other end — a quarter less burst for a quarter less parallelism.
#   Teams on a high rate tier can raise it with `max_concurrency`.
# - Local providers used to get
#   1, on the reasoning that a local server processes one request at a time so a
#   wider pool would only queue. The queueing is real, but the conclusion was
#   wrong in both directions: a server that CAN batch was capped at 1 for no
#   reason, and a server that cannot loses nothing by having work queued for it —
#   ollama queues (up to OLLAMA_MAX_QUEUE, 512) rather than failing, so the wall
#   clock is the server's throughput either way.
#
#   The knob that actually decides local throughput is on the SERVER, not here:
#   `OLLAMA_NUM_PARALLEL` (1 by default), llama.cpp's `-np`, vLLM's batching.
#   Raising those costs memory in proportion — ollama allocates context per
#   parallel slot, and llama.cpp splits one KV cache across slots, so `-c 32768
#   -np 4` leaves each slot 8k, well under a single review prompt. vLLM is the
#   exception: it batches and keeps full `--max-model-len` per request.
#
#   The one case for lowering this back to 1 is a very slow local model, where
#   six queued calls could each wait out the per-request timeout — and the local
#   per-call default is scaled by this width precisely so that they do not.
#
# There is deliberately no per-provider exception list. If one is ever needed
# again, keep it a value a reader can see next to the branch that reads it: the
# last one was a named-but-empty set two hundred lines away, which read as a
# policy it no longer had.
_DEFAULT_MAX_WORKERS = 6
# The fast preset's `code-health` group id is gated too: a finding stamped with
# it carried no member category, so it cannot prove it is one of the exempt
# members (complexity, ponytail) rather than a gated one (performance,
# deprecation). An attributed exempt member keeps its own category and stays
# exempt. `artefacts` holds only exempt members, so it needs no entry.
_FAILURE_SCENARIO_CATEGORIES: frozenset[str] = frozenset(
    {
        ReviewCategory.security.value,
        ReviewCategory.correctness.value,
        ReviewCategory.deprecation.value,
        ReviewCategory.performance.value,
        CODE_HEALTH_GROUP.id,
    }
)

# Don't warm the cache for a small diff. On wall-clock the primer is roughly
# neutral — either the primer pays the one uncached pass and the rest read, or
# a concurrent first wave all pays it in parallel — so the primer's real win
# is cost and rate-limit headroom: N-1 concurrent misses each re-process the
# full prefix uncached AND each pay the cache-write surcharge (1.25× input
# price on anthropic), while the primer pays it once. That waste scales with
# prefix size; the primer's worst case stays one call of lost parallelism.
# Below ~2k tokens of wrapped diff the waste is too small to buy anything —
# keep full concurrency. (Simulated A/B on a 5k-token prefix, 9 lenses:
# warm-up ≈ +4s wall, −39k cache-write tokens.)
#
# Deliberately NOT gated on a provider list. The primer is about the shape of
# the first wave, not about the cache_control marker: a backend that caches
# automatically (OpenAI, Azure, DeepSeek — including via openrouter) pays the
# same N-way miss when N identical prefixes arrive at once, and profits from
# the same fix. A provider allowlist here would only be a second copy of the
# adapter's route list, drifting from it every time a backend adds caching.
_WARMUP_MIN_TOKENS = 2048


def concurrency_cap(cfg: ReviewConfig) -> int:
    """How many model calls this run may have in flight: the explicit cap, else
    the common default — one number, not a per-provider one.

    A property of the *backend*, independent of how much work there is — which
    is why it is separate from the pool size below. The oversized-batch split
    runs its pieces in a pool of its own, and needs this figure rather than the
    fan-out's: a one-lens review sizes its pool to one task, but that says
    nothing about what the provider will serve at once.
    """
    if cfg.limits.max_concurrency is not None:
        return max(1, cfg.limits.max_concurrency)
    return _DEFAULT_MAX_WORKERS


def _resolve_workers(cfg: ReviewConfig, task_count: int) -> int:
    """The fan-out pool size: the cap above, narrowed to the work there is."""
    return max(1, min(concurrency_cap(cfg), task_count))


@dataclass(frozen=True)
class _PreparedReview:
    """The work of one review, ready for the fan-out; see ``_prepare_review``.

    The redacted, filtered, scanned and batched calls (``per_batch`` and
    ``workers``) plus the filter and scan state the post-fan-out pipeline and
    the summary notices report on.
    """

    cfg: ReviewConfig
    # The run every prepared call shares: its metered provider for the calls
    # after the fan-out (reflection), and its state for the summary notices.
    run: _Run
    lenses: list[_Lens]
    per_batch: list[tuple[bool, list[_PreparedCall]]]
    workers: int
    # The reviewed files after every filter (triage, file cap, expansion).
    file_patches: list[tuple[str, str]]
    scan_findings: list[ReviewFinding]
    # The diff exactly as the scan corpus saw it, before triage and the file
    # cap narrowed file_patches; scan findings snap against this.
    scan_scope: str
    # Static-analysis hints on scan-only files, and the lens carrying them.
    unbatched: list[ToolFinding]
    carrier: _Lens | None
    # Filter outcomes the summary names rather than silently dropping.
    oversized: list[str]
    skipped_by_triage: list[str]
    total_files: int
    capped_files: bool
    unbatched_hints_dropped: int
    spec_unfit: list[str]
    spec_reviewed: bool


def _lens_messages(
    run: _Run,
    wrapped: str,
    intent_block: str | None,
    hint_block: str | None,
    dir_block: str | None,
    lens: _Lens,
    *,
    spec_block: str | None = None,
    hidden_block: str | None = None,
    context: str | None = None,
) -> list[Message]:
    """The split-prefix messages for one lens call.

    ``context`` is the fetched-for-a-deferral file text (see
    :meth:`LLMReviewEngine._review_with_context`). It rides the lens's own block —
    never the shared prefix: that prefix is the cache entry this batch's sibling
    lenses read, and one deferral must not make every one of them miss. It sits
    ahead of the lens checklist for the same reason the diff does, so the trusted
    instructions stay closest to the answer.
    """
    # Only the intent lens pays the intent-block tokens (and its injection
    # surface); the other lenses never see PR-authored prose. It rides the lens
    # block — NOT the shared prefix — so their cached prefix
    # stays identical. The spec block is gated the same way, for the same two
    # reasons: it is large, and it is untrusted.
    intent = intent_block if lens.carries_intent else None
    spec = spec_block if lens.carries_spec else None
    retrieval = run.retrieval_budget is not None
    # The directory block joins the ONE prefix string rather than adding a
    # fourth message: the adapter puts its cache breakpoint on the last prefix
    # block, and it varies per batch exactly like the hints do, so it is warmed
    # once by the primer and read by lenses 2..N.
    prefix = "\n\n".join(
        part
        for part in (dir_block, hint_block, run.stack_block, hidden_block, wrapped)
        if part is not None
    )
    suffix = lens.user_block if context is None else f"{context}\n\n{lens.user_block}"
    if spec is not None:
        suffix = f"{spec}\n\n{suffix}"
    if intent is not None:
        suffix = f"{intent}\n\n{suffix}"
    return [
        {"role": "system", "content": build_shared_preamble(run.language, retrieval, run.project)},
        {"role": "user", "content": prefix},
        {"role": "user", "content": suffix},
    ]


class ReviewIncompleteError(Exception):
    """No usable result: every review call failed (timeout or unparseable
    output), or no lens could run over a diff that had code to review.

    Raised instead of silently reporting a clean review, so the CLI surfaces a
    failure (non-zero exit / failure comment) rather than a false 👍 LGTM.
    """


class LLMReviewEngine:
    """Review engine that runs the full pipeline against an injected ProviderClient."""

    def __init__(
        self,
        provider: ProviderClient,
        fetch_file: FileFetcher | None = None,
        resolve_symbol: SymbolResolver | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self._provider = provider
        # The checked-out repository the engine may READ from: directory-rule
        # context files and the committed spec. On `pull_request_target` this is
        # the trusted BASE branch — never the PR head — which is the whole reason
        # those two read from a workspace instead of the gateway. Defaults to the
        # process's cwd, which is what both the Action and the local CLI want;
        # injectable so the eval harness can point it at a fixture corpus.
        self._workspace_root = workspace_root or Path.cwd()
        # Optional read-only file reader for the reflection pass's bounded retrieval
        # escalation: when the auditor defers a finding for lack of a referenced
        # file, this fetches it (read-only — never a checkout) so it can re-judge.
        # None (the default) keeps the prior behavior: a deferral can't resolve, so
        # the unverifiable finding is dropped.
        self._fetch_file = fetch_file
        # Optional ast-grep symbol resolver: when the auditor defers by naming a
        # SYMBOL (not a path), this maps it to the file that defines it so the
        # fetcher above can pull it. None keeps the prior path-only behaviour.
        self._resolve_symbol = resolve_symbol

    def _recovery(self) -> SupportsRecovery | None:
        """The adapter's recovery steps, or None when it offers none.

        One probe for every step the engine may ask of the adapter (see
        ``SupportsRecovery``), beyond the frozen one-method ``ProviderClient``
        port. A provider without it simply never takes those steps, rather than
        every fake in the suite growing them; the engine still decides which step
        a failure gets.
        """
        return self._provider if isinstance(self._provider, SupportsRecovery) else None

    def _ladder(self) -> _Ladder:
        """The recovery ladder for one lens call, wired back into this engine.

        Built per call, like :meth:`_recovery`: the seam is this engine's own
        ``_complete_lens`` (a re-issued call) and ``_review_lens`` (a split
        piece), and the adapter's recovery steps as probed now.
        """
        return _Ladder(self._recovery(), self._complete_lens, self._review_lens)

    def _risk(self, ctx: PRContext, cfg: ReviewConfig) -> RiskAssessment | None:
        """The risk-of-change verdict for the summary; None when risk is off."""
        if not cfg.risk.enabled:
            return None
        if ctx.risk is not None:
            return ctx.risk
        with profiler.stage("risk"):
            return assess_risk(ctx, cfg, self._workspace_root)

    def _fit_input_budget(self, cfg: ReviewConfig) -> ReviewConfig:
        """Shrink the default batching budget to what the model can actually take.

        `max_input_tokens` defaults to a flat number that knows nothing about the
        model: a 100k batch to a 65k-window model is refused outright, and to an
        ollama server it is silently cut to `num_ctx` — the review reads a
        fraction of the diff and reports on the rest as if it had. A provider
        that can say how much prompt fits (feature-detected: the litellm adapter
        reads litellm's model map, or ollama's `num_ctx`, less the output
        ceiling it will send) is asked, and the default is capped at its answer.

        A value the user configured is left alone — they may know something the
        map does not, and the split on a refused prompt still covers them. A
        provider with no method, or no opinion, changes nothing. Only ever
        down, never up: the default is also a spend ceiling.
        """
        # The group's own fields_set: setting a sibling (`context_lines`) marks
        # the group set, never this field, so a defaulted budget is still fitted.
        if "max_input_tokens" in cfg.batching.model_fields_set:
            return cfg
        recovery = self._recovery()
        if recovery is None:
            return cfg
        try:
            budget = recovery.input_budget()
        except Exception:  # noqa: BLE001 — a capability lookup must never fail a review
            _log.warning("could not read the model's input budget", exc_info=True)
            return cfg
        if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
            return cfg
        if budget >= cfg.batching.max_input_tokens:
            return cfg
        _log.info(
            "max_input_tokens fitted to the model's context window",
            extra={"default": cfg.batching.max_input_tokens, "fitted": budget},
        )
        batching = cfg.batching.model_copy(update={"max_input_tokens": budget})
        return cfg.model_copy(update={"batching": batching})

    def review(self, ctx: PRContext, cfg: ReviewConfig) -> tuple[list[ReviewFinding], str]:
        """Run the review pipeline and return (findings, summary)."""
        cfg = self._fit_input_budget(cfg)
        # First, while `ctx` is still whatever the caller handed in: a verdict it
        # already carries was assessed on the WHOLE pull request (the CLI does so
        # before incremental scoping narrows the diff), so it is reported as is.
        risk = self._risk(ctx, cfg)
        prepared = self._prepare_review(ctx, cfg)

        all_findings: list[ReviewFinding] = []
        total_calls = 0
        failed_calls = 0
        errors: list[str] = []
        # The lens ids behind those errors, in the same order — the summary names
        # them, because "1 of 4 failed" says nothing about whether to worry.
        failed_lenses: list[str] = []

        # 5. Fan out one call per (batch, lens) through ONE global pool. The old
        #    shape — a fresh pool per batch, joined before the next batch starts —
        #    cost `batches × ceil(lenses / workers)` sequential full-latency
        #    waves; flattened, it is `ceil(batches × lenses / workers)`. Each
        #    lens gets a focused prompt; findings are merged afterwards.
        #    Concurrency is provider-aware (see _resolve_workers).
        with profiler.stage("review"):
            # Results keyed by task index and consumed in task order, so the
            # findings stay deterministic (dedupe's first-wins tiebreak is
            # order-sensitive) whatever order the futures complete in. Zipped
            # back against the calls in that same order so a failure can name
            # the lens it lost.
            calls = [call for _, batch_calls in prepared.per_batch for call in batch_calls]
            for call, (findings, error) in zip(
                calls, self._fan_out(prepared.per_batch, prepared.workers, calls), strict=True
            ):
                total_calls += 1
                if error is not None:
                    failed_calls += 1
                    errors.append(error)
                    failed_lenses.append(call.lens_id)
                all_findings.extend(findings)

        # 5b. Fail loud: if EVERY call errored or returned unparseable output, we
        #     have no signal — never pass that off as a clean review. Findings are
        #     part of that test now that a call can be partially successful: a
        #     timed-out batch whose split produced findings from one piece and a
        #     failure from another has real signal to post, and throwing it away to
        #     raise "every review call failed" would lose a genuine finding. The
        #     failure still reaches the summary through the incomplete-results
        #     notice, so nothing is hidden either way.
        if (
            total_calls > 0
            and failed_calls == total_calls
            and not all_findings
            and not prepared.scan_findings
        ):
            # The most common error, not the last — see _build_notices.
            detail = Counter(errors).most_common(1)[0][0] if errors else "no usable output"
            hint = f" {_SCHEMA_DROP_NOTE}" if prepared.run.state.schema_dropped else ""
            raise ReviewIncompleteError(
                f"review incomplete: every review call failed ({detail}). "
                "Check the provider credentials/quota, model, and timeout "
                f"(ollama: a larger model needs a longer --timeout), then retry.{hint}"
            )

        return self._collect_findings(
            ctx,
            prepared,
            findings=all_findings,
            errors=errors,
            failed_lenses=failed_lenses,
            total_calls=total_calls,
            failed_calls=failed_calls,
            risk=risk,
        )

    def _collect_findings(
        self,
        ctx: PRContext,
        prepared: _PreparedReview,
        *,
        findings: list[ReviewFinding],
        errors: list[str],
        failed_lenses: list[str],
        total_calls: int,
        failed_calls: int,
        risk: RiskAssessment | None,
    ) -> tuple[list[ReviewFinding], str]:
        """Stages 5c to 9b plus the summary: everything after the fan-out.

        The model's findings join the deterministic scan findings, then snap,
        scope, suppress, merge, reflect, arbitrate and filter; the summary and
        its notices are built from what survived and the run's state.
        """
        cfg = prepared.cfg
        reviewed_diff = "\n".join(patch for _, patch in prepared.file_patches)

        # 5c. Deterministic findings join the model's here — before snapping so
        #     they get re-anchored, suppressed and rule-filtered by exactly the
        #     same stages. Dedupe never merges them with a model finding (see
        #     `_dedupe`), and reflection is the one stage they skip; see step 8.
        all_findings = findings + prepared.scan_findings

        with profiler.stage("snap"):
            all_findings, hinted_no_line_paths = _snap_to_diff(
                all_findings, ctx, prepared, reviewed_diff
            )
        all_findings, off_diff = _scope_scans(all_findings)
        with profiler.stage("suppress"):
            all_findings, suppressed = _suppress(all_findings, cfg, ctx)
        if suppressed:
            _log.info("suppressed findings", extra={"count": suppressed})
        all_findings = _cap_scans(all_findings, off_diff)

        # 7b. Evidence gate: built-in defect findings must name a concrete way
        #     the changed code fails. Category is engine-stamped, so lowering the
        #     model-selected severity cannot bypass this check.
        with profiler.stage("failure_scenario"):
            all_findings = _filter_missing_failure_scenarios(all_findings)

        # 7c. Merge: a finding can surface under more than one lens (a shell
        #     injection is both a security and a correctness issue), so collapse
        #     duplicates before reflecting. Arbitration runs first: the merge
        #     selects by severity, which would let an opposed lens's copy win
        #     the shared location before either referee saw the pair, posting
        #     the code-cutting advice and losing the fix (issue #752).
        with profiler.stage("dedupe"):
            all_findings = _dedupe(resolve_conflicts(all_findings))

        # 8. Self-reflection; scan findings sit it out (see _reflect).
        scanned = [f for f in all_findings if _is_scan_finding(f)]
        all_findings, reflection_skipped, reflection_notices = self._reflect(
            [f for f in all_findings if not _is_scan_finding(f)], ctx, prepared, reviewed_diff
        )

        # 8a. Arbitrate opposing advice: a lens that cuts code yields to one that
        #     fixes the same lines. Reflection was asked to arbitrate too; this is
        #     the deterministic referee, so it also holds under --no-reflect.
        with profiler.stage("arbitrate"):
            all_findings = resolve_conflicts(all_findings)

        # 8b. Rejoin the deterministic findings the auditor never saw.
        all_findings = all_findings + scanned

        with profiler.stage("filter"):
            filtered = _apply_floors(all_findings, cfg, hinted_no_line_paths)

        return filtered, _summarise(
            ctx,
            prepared,
            filtered,
            errors=errors,
            failed_lenses=failed_lenses,
            total_calls=total_calls,
            failed_calls=failed_calls,
            suppressed=suppressed,
            off_diff=off_diff,
            reflection_skipped=reflection_skipped,
            reflection_trimmed=bool(reflection_notices),
            risk=risk,
        )

    def _reflect(
        self,
        findings: list[ReviewFinding],
        ctx: PRContext,
        prepared: _PreparedReview,
        reviewed_diff: str,
    ) -> tuple[list[ReviewFinding], str | None, list[str]]:
        """Stage 8: the self-reflection audit of the model's findings.

        Returns the surviving findings, the name of the ceiling that stopped the
        audit (None when it ran or was off), and the audit's trim notices.
        """
        cfg = prepared.cfg
        # 8. Self-reflection: filter out low-confidence findings. Reflect against
        #    only the reviewed diff — redacted, and free of skipped/over-cap files.
        #    Skippable (--no-reflect) for weaker models that drop valid findings here.
        #    Scan findings sit this stage out: the auditor's job is to catch a
        #    model talking itself into a false positive, and there is no model
        #    here to audit. Partitioned rather than filtered afterwards so they
        #    cost no reflection tokens and the auditor cannot drop them.
        # None, or the name of the ceiling that stopped the audit — it feeds the
        # notice below, so a skipped audit always says which knob to raise.
        reflection_skipped: str | None = None
        # Trim notices from the audit (diff narrowed to fit the input budget).
        reflection_notices: list[str] = []
        if cfg.reflect and findings:
            # Overrunning a ceiling is NOT a reason to skip the audit. The lens
            # fan-out already stops short of both ceilings (`lens_deadline_at` /
            # `lens_budget_at`) to leave the auditor room, and a review that
            # overran still needs pruning: measured, a runaway lens call passed
            # the deadline and 325 unaudited findings posted, 323 of them false
            # positives on a diff with nothing wrong in it. Reflection is a
            # single bounded call, so running it past the ceiling is cheaper than
            # posting that many unaudited findings.
            #
            # A termination signal is the only exception: the process is being
            # torn down on someone else's clock, so there is no budget to reserve.
            # Skip the audit, post what we have, and record why in the summary.
            if interrupt_requested():
                reflection_skipped = "Review interrupted (termination signal)"
                _log.warning(
                    "review interrupted — skipping reflection",
                    extra={"findings": len(findings)},
                )
            else:
                _log.info("reflecting on findings", extra={"findings": len(findings)})
                # model_copy keeps file_contents — reflection now grounds itself
                # on the (redacted) head text of flagged files to verify
                # whole-file claims.
                clean_ctx = ctx.model_copy(update={"diff": reviewed_diff})
                with profiler.stage("reflect"):
                    findings = reflect_findings(
                        findings,
                        clean_ctx,
                        cfg,
                        prepared.run.provider,
                        fetch_file=self._fetch_file,
                        resolve_symbol=self._resolve_symbol,
                        # The hints a lockfile finding was confirmed from: the
                        # auditor's only evidence for a file the diff never shows.
                        scan_hints=prepared.unbatched,
                        notices=reflection_notices,
                    )
        return findings, reflection_skipped, reflection_notices

    def _prepare_review(self, ctx: PRContext, cfg: ReviewConfig) -> _PreparedReview:
        """Stages 1 to 4 of the pipeline: redact, filter, scan, batch, arm the calls.

        Everything up to (but not including) the fan-out: the run-scoped
        ceilings and per-review state, the lens set, and one prepared call per
        (batch, lens) in ``per_batch``. Each stage is a function in
        ``stages.py``; this method only runs them in order and threads their
        results along. Raises :class:`ReviewIncompleteError` when there is
        code to review but no lens could run over it.
        """
        # First, so the deadline counts every stage, not just the model calls.
        lens_deadline_at, lens_budget_at = lens_ceilings(cfg)
        # This review's own record of what happened (notices, spend), built
        # here and carried on the run, never on `self`: one engine may serve
        # two reviews at once.
        state = _RunState()
        provider = _MeteredProvider(self._provider, state)

        clean_diff, clean_intent = redact_inputs(ctx)
        spec = spec_context(cfg, ctx, self._workspace_root)
        clean_spec = spec.text
        lenses = _build_lenses(
            cfg,
            has_intent=clean_intent is not None,
            has_spec=clean_spec is not None,
        )

        file_patches, oversized = filter_files(clean_diff, ctx, cfg)
        total_files = len(file_patches)

        # 2c. Fail loud when there is code to review but no lens to review it:
        #     every configured lens was skipped (intent with nothing stated, spec
        #     with no matching specification) or none was configured. Zero calls
        #     would otherwise slip past 5b's "every call failed" guard and post
        #     👍 LGTM over a diff nothing looked at.
        if file_patches and not lenses:
            raise ReviewIncompleteError(
                "review incomplete: no review lens could run for the configured "
                "categories (intent needs a stated intent; spec needs a committed "
                "specification matching this change). Add another category to "
                "`categories`, or remove the override to run the default set."
            )

        scan = scan_files(file_patches, ctx, cfg)
        file_patches, skipped_by_triage = triage_patches(
            file_patches, scan.tool_findings, cfg, provider
        )
        file_patches, capped_files = cap_files(file_patches, cfg)
        file_patches = expand_context(file_patches, ctx, cfg)
        batches = batch_patches(file_patches, lenses, cfg)
        dir_contents = directory_context(cfg, self._workspace_root)

        # Announce the queued work before any model call returns, so a long run
        # on a slow provider shows up immediately in the Action log instead of
        # looking stuck until the first review comment lands.
        _log.info(
            "review starting",
            extra={
                "reviewable_files": len(file_patches),
                "batches": len(batches),
                "lenses": len(lenses),
            },
        )

        run = _new_run(
            cfg,
            ctx,
            provider,
            state,
            (lens_deadline_at, lens_budget_at),
            can_fetch=self._fetch_file is not None,
        )
        workers = _resolve_workers(cfg, len(batches) * len(lenses))
        unbatched = carry_unbatched_hints(scan.hints, ctx.scan_contents, batches, lenses)
        blocks_for = blocks_builder(ctx, cfg, scan.hints, clean_intent, clean_spec, dir_contents)
        per_batch = self._arm_calls(
            run, batches, lenses, workers, blocks_for, unbatched, ctx.changed_files
        )

        return _PreparedReview(
            cfg=cfg,
            run=run,
            lenses=lenses,
            per_batch=per_batch,
            workers=workers,
            file_patches=file_patches,
            scan_findings=scan.findings,
            scan_scope=scan.scope,
            unbatched=unbatched.hints,
            carrier=unbatched.carrier,
            oversized=oversized,
            skipped_by_triage=skipped_by_triage,
            total_files=total_files,
            capped_files=capped_files,
            unbatched_hints_dropped=unbatched.dropped,
            spec_unfit=list(spec.unfit),
            spec_reviewed=clean_spec is not None,
        )

    def _arm_calls(
        self,
        run: _Run,
        batches: list[list[tuple[str, str]]],
        lenses: list[_Lens],
        workers: int,
        blocks_for: _BlocksFor,
        unbatched: UnbatchedHints,
        changed_files: Sequence[str],
    ) -> list[tuple[bool, list[_PreparedCall]]]:
        """One prepared call per (batch, lens), and whether each batch warms the cache.

        The first batch's carrier lens is swapped for the copy that carries the
        unbatched hints (see :func:`stages.carry_unbatched_hints`).
        """
        per_batch: list[tuple[bool, list[_PreparedCall]]] = []
        for batch_num, batch in enumerate(batches, start=1):
            batch_diff = "\n".join(patch for _, patch in batch)
            wrapped = wrap_diff(batch_diff)
            # Static-analysis hints for THIS batch's files only, redacted (tool
            # messages can quote hostile file content — same posture as the
            # diff) and wrapped as their own neutralised untrusted block.
            batch_paths = {path for path, _ in batch}
            not_visible = files_not_visible(changed_files, batch_paths)
            intent_block, hint_block, dir_block, spec_block = blocks_for(batch_paths)
            # What this call was NOT shown, for EVERY lens — not just the two that
            # judge a whole-PR promise. Absence stated as fact beats absence
            # inferred from "code you rely on may live in files you CANNOT see",
            # which asks the model to reason about what it cannot observe. None
            # when the batch shows the whole PR, so the common case adds nothing
            # to the cached prefix.
            hidden_block = wrap_not_shown(not_visible)
            # Warm the prompt cache for this batch: a fully concurrent first
            # wave defeats it (every call misses, and on explicit-breakpoint
            # routes each also pays the cache write), so one lens is dispatched
            # alone and the rest of the batch releases on its completion —
            # reading the shared preamble-plus-diff prefix instead of
            # re-writing it. Gated on diff size (see _WARMUP_MIN_TOKENS) so a
            # small diff keeps full concurrency, and on a fan-out wide enough
            # for there to be a wave at all.
            warm = workers > 1 and len(lenses) > 1 and count_tokens(wrapped) >= _WARMUP_MIN_TOKENS
            carrier_lens = unbatched.carrier_lens
            batch_calls = [
                _PreparedCall(
                    lens_id=lens.id,
                    task=partial(
                        self._review_lens,
                        run,
                        wrapped,
                        intent_block,
                        hint_block,
                        dir_block,
                        batch_num,
                        carrier_lens
                        if carrier_lens is not None and batch_num == 1 and lens is unbatched.carrier
                        else lens,
                        batch,
                        spec_block=spec_block,
                        hidden_block=hidden_block,
                        blocks_for=blocks_for,
                    ),
                )
                for lens in lenses
            ]
            per_batch.append((warm, batch_calls))
        return per_batch

    def _fan_out(
        self,
        per_batch: list[tuple[bool, list[_PreparedCall]]],
        workers: int,
        calls: list[_PreparedCall],
    ) -> list[_LensOutcome]:
        """Run every (batch, lens) call through one pool; results in call order.

        ``calls`` is ``per_batch`` flattened, built once by the caller and shared
        so the outcomes stay aligned with the calls they answer.

        Batches flagged for cache warm-up submit only their FIRST lens; the
        rest of that batch is released when the primer completes (having
        written the shared prefix to the provider's prompt cache). Unflagged
        batches submit everything up front. Cross-batch work interleaves
        freely — batch 2's primer runs while batch 1's followers are in
        flight, so warming never re-serialises the whole review.

        Whatever failed transiently then gets one more go; see :func:`recovery._rescue`.
        """
        results: dict[int, _LensOutcome] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending: dict[Future[_LensOutcome], int] = {}
            primer_batch: dict[Future[_LensOutcome], int] = {}
            deferred: dict[int, list[tuple[int, _PreparedCall]]] = {}
            index = 0
            for batch_index, (warm, batch_calls) in enumerate(per_batch):
                indexed = list(enumerate(batch_calls, start=index))
                index += len(batch_calls)
                if warm:
                    primer_index, primer = indexed[0]
                    future = pool.submit(primer.task)
                    pending[future] = primer_index
                    primer_batch[future] = batch_index
                    deferred[batch_index] = indexed[1:]
                else:
                    for call_index, call in indexed:
                        pending[pool.submit(call.task)] = call_index
            while pending:
                # wait() copies its argument internally, so passing the dict's
                # keys directly avoids building a second throwaway set per loop.
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    results[pending.pop(future)] = future.result()
                    batch_index = primer_batch.pop(future, -1)
                    if batch_index >= 0:
                        # Primer done (pass or fail — a failed primer must not
                        # strand its batch): release the deferred lens calls.
                        for call_index, call in deferred.pop(batch_index, []):
                            pending[pool.submit(call.task)] = call_index
        return _rescue(calls, [results[i] for i in sorted(results)])

    def _review_lens(
        self,
        run: _Run,
        wrapped: str,
        intent_block: str | None,
        hint_block: str | None,
        dir_block: str | None,
        batch_num: int,
        lens: _Lens,
        batch: list[tuple[str, str]] | None = None,
        *,
        spec_block: str | None = None,
        hidden_block: str | None = None,
        blocks_for: _BlocksFor | None = None,
    ) -> tuple[list[ReviewFinding], str | None]:
        """Run one focused review call for a single lens (built-in or user-defined).

        Returns ``(findings, error)``. ``error`` is None on success, else a concise
        reason — the provider exception (e.g. a 429 quota error) or unparseable
        output — that the engine surfaces so a failure names its real cause instead
        of a generic "timeout". A failing lens never aborts the others.

        ``batch`` is the file patches ``wrapped`` was built from. Supplying it opts
        this call into the one retry an oversized payload can actually benefit
        from: the payload is split and the pieces reviewed (see
        ``_Ladder._review_split``). None means "already a piece" — no further
        splitting. ``blocks_for`` rebuilds the per-batch blocks for each piece's
        own file set; None means there is nothing smaller to rebuild for.

        Every call uses the shared system preamble, then the shared prefix
        (hints + diff) as one user message, then the lens block (intent +
        checklist + example) as a final user message. Caching providers serve
        the identical expensive prefix from cache.

        ``run.retrieval_budget`` (None = off) opts this call into the one deferral
        a lens may make: answering with ``needs``, it is re-run once with that code
        fetched read-only (see :meth:`_review_with_context`). Like the
        oversized-payload split, it is offered only when ``batch`` is supplied —
        so a re-run, which passes none, can never defer again.
        """
        skip = _skip_reason(run, lens)
        if skip is not None:
            return [], skip
        on_oversized = (
            None
            if batch is None
            else partial(
                self._ladder()._review_split,
                run=run,
                batch=batch,
                intent_block=intent_block,
                hint_block=hint_block,
                dir_block=dir_block,
                batch_num=batch_num,
                lens=lens,
                spec_block=spec_block,
                hidden_block=hidden_block,
                blocks_for=blocks_for,
            )
        )
        on_needs = (
            None
            if batch is None or run.retrieval_budget is None
            else partial(
                self._review_with_context,
                run=run,
                wrapped=wrapped,
                batch=batch,
                intent_block=intent_block,
                hint_block=hint_block,
                dir_block=dir_block,
                batch_num=batch_num,
                lens=lens,
                spec_block=spec_block,
                hidden_block=hidden_block,
            )
        )
        messages = _lens_messages(
            run,
            wrapped,
            intent_block,
            hint_block,
            dir_block,
            lens,
            spec_block=spec_block,
            hidden_block=hidden_block,
        )
        return self._complete_lens(
            messages,
            run.response_format,
            batch_num,
            lens,
            on_oversized,
            on_needs,
            run=run,
        )

    def _review_with_context(
        self,
        needs: list[str],
        findings: list[ReviewFinding],
        *,
        run: _Run,
        wrapped: str,
        batch: list[tuple[str, str]],
        intent_block: str | None,
        hint_block: str | None,
        dir_block: str | None,
        batch_num: int,
        lens: _Lens,
        spec_block: str | None = None,
        hidden_block: str | None = None,
    ) -> _LensOutcome:
        """Re-run one lens with the bounded, read-only code it asked to read.

        The shared rules tell every lens the diff is a slice of the codebase, so a
        claim resting on unshown code must be hedged or dropped — precision bought
        by throwing recall away. This is the third option: the lens names what it
        must read (``needs``), that text is fetched through the SAME read-only,
        redacting boundary reflection's deferral uses (never a checkout — fork-safe),
        and the lens answers once more with it in front of it.

        Bounded on every axis. One hop (the re-run is issued with ``batch=None``, so
        a second ``needs`` is ignored); at most ``MAX_FETCH_FILES`` files inside
        ``run.retrieval_budget`` tokens; and the deadline/budget guards are
        re-checked first, so a deferral arriving past a ceiling degrades into the
        existing incomplete-results notice instead of spending past it.

        Returns ``first + retry`` findings, left for the pipeline's ``_dedupe`` to
        collapse. Replacing the first call's findings wholesale would be simpler and
        wrong: the lens was already confident about those, and the deferral was about
        something else. The cost is a duplicate pair when the re-run repeats itself —
        which dedupe (keyed path/line/side) is exactly what removes.
        """
        skip = _skip_reason(run, lens)
        if skip is not None:
            # The lens asked for code we may no longer spend on. Keep what it
            # already found and report the skip: the run IS incomplete, and the
            # existing notice is how that reaches the PR.
            return findings, skip
        # Never offered without both; the guard is here so the types line up.
        if self._fetch_file is None or run.retrieval_budget is None:  # pragma: no cover
            return findings, None
        fetched = resolve_needs(
            needs,
            self._fetch_file,
            already={path for path, _ in batch},
            budget_tokens=run.retrieval_budget,
            max_files=MAX_FETCH_FILES,
            resolve_symbol=self._resolve_symbol,
        )
        if not fetched:
            # Nothing readable came back (bad paths, a symbol with no definition,
            # everything over budget). Re-asking with no new information would buy
            # a second identical answer, so keep the first one.
            _log.info(
                "lens deferral resolved to nothing — keeping the first answer",
                extra={"lens": lens.id, "batch": batch_num, "needs": needs},
            )
            return findings, None
        _log.info(
            "lens deferred for context — re-reviewing with fetched files",
            extra={"lens": lens.id, "batch": batch_num, "files": sorted(fetched)},
        )
        messages = _lens_messages(
            run,
            wrapped,
            intent_block,
            hint_block,
            dir_block,
            lens,
            spec_block=spec_block,
            hidden_block=hidden_block,
            context=wrap_context(fetched),
        )
        retry, error = self._complete_lens(
            messages, run.response_format, batch_num, lens, None, None, run=run
        )
        return findings + retry, error

    def _complete_lens(
        self,
        messages: list[Message],
        response_format: type[ReviewResult] | None,
        batch_num: int,
        lens: _Lens,
        on_oversized: _OnOversized | None = None,
        on_needs: Callable[[list[str], list[ReviewFinding]], _LensOutcome] | None = None,
        *,
        effort: dict[str, Any] | None = None,
        recover: bool = True,
        escalate_to: str | None = None,
        run: _Run,
    ) -> tuple[list[ReviewFinding], str | None]:
        """The provider call + parse + stamp shared by both prompt shapes.

        A failure goes to the recovery ladder (``recovery._Ladder``): a raised
        call to ``_call_failed``, a reply that did not parse to ``_unparseable``,
        and one whose body stopped mid-answer to ``_cut_off``.

        ``on_oversized`` handles the failures that say something about the
        *payload* rather than the provider: the call outlived its entire budget,
        or its answer hit the model's output ceiling. Both mean one call was
        asked to cover more than it could finish, and both are fixed by covering
        less. It is given the failure reason and returns the outcome to use
        instead. None when there is nothing left to split.

        ``on_needs`` is its twin on the SUCCESS path: the lens parsed, but
        answered that it must read code outside the diff before it can decide. It
        is given the requested paths and the findings this call did make, and
        returns the outcome to use instead. None (a re-run, or the feature off)
        means a ``needs`` in the response is simply ignored — nothing is parsed
        for it, so a review without the feature costs exactly what it did before.

        ``effort``, ``recover`` and ``escalate_to`` mark a call that a rung of
        the ladder issued, which bounds each rung to one attempt; see
        ``recovery._Reissue``.
        """
        ladder = self._ladder()
        opts: dict[str, Any] = {"response_format": response_format} if response_format else {}
        if effort is not None:
            opts.update(effort)
        if escalate_to is not None:
            opts["model_override"] = escalate_to
        # A lens call owns its own truncation remedy — the payload and the token
        # counts are up here — so the adapter is told to hand one back rather
        # than spend its fallback on the identical oversized request first. Every
        # other failure it still rescues itself, exactly as before.
        opts["defer_truncation"] = True
        # Heartbeat: log the call going out and coming back so the Action shows
        # steady per-lens progress while the model runs, not a silent gap.
        _log.info("reviewing lens", extra={"lens": lens.id})
        started = time.perf_counter()
        try:
            result = run.provider.complete(messages, model=run.model, **opts)
        except Exception as exc:
            return ladder._call_failed(
                exc,
                started,
                messages=messages,
                response_format=response_format,
                batch_num=batch_num,
                lens=lens,
                run=run,
                on_oversized=on_oversized,
                effort=effort,
                escalate_to=escalate_to,
            )
        elapsed = time.perf_counter() - started
        ladder._note_answering_model(result, lens, run)
        try:
            findings = parse_findings(result.text)
        except ParseError as exc:
            if not exc.truncated:
                return ladder._unparseable(
                    exc,
                    result,
                    elapsed,
                    messages=messages,
                    response_format=response_format,
                    batch_num=batch_num,
                    lens=lens,
                    run=run,
                    recover=recover,
                )
            return ladder._cut_off(
                exc,
                result,
                elapsed,
                messages=messages,
                response_format=response_format,
                batch_num=batch_num,
                lens=lens,
                run=run,
                on_oversized=on_oversized,
                effort=effort,
                escalate_to=escalate_to,
            )
        findings = _stamp_and_bound(findings, lens, run)
        profiler.record_result(
            lens.id,
            batch_num,
            elapsed,
            result,
            findings=len(findings),
        )
        if on_needs is not None:
            needs = parse_needs(result.text)
            if needs:
                return on_needs(needs, findings)
        _log.info("lens reviewed", extra={"lens": lens.id, "findings": len(findings)})
        return findings, None


def _is_droppable_scan(finding: ReviewFinding) -> bool:
    """A scan finding the diff-scoping rule may drop when it fails to anchor.

    Dependency findings are exempt — they are unanchorable by construction, so
    dropping them for failing to anchor would delete every one of them.
    """
    return _is_scan_finding(finding) and finding.category not in UNANCHORABLE_SCAN_CATEGORIES


def _is_scan_finding(finding: ReviewFinding) -> bool:
    """Whether *finding* came from a deterministic tool rather than a lens.

    Keyed on the engine-stamped `scan:` category prefix. Lens ids cannot collide
    with it: `ReviewConfig` rejects a custom lens whose id starts with the
    prefix, so a model finding can never be mistaken for a tool's.
    """
    return (finding.category or "").startswith(SCAN_CATEGORY_PREFIX)


def _passes_severity_floor(
    finding: ReviewFinding,
    cfg: ReviewConfig,
    *,
    no_line_paths: Collection[str] = frozenset(),
) -> bool:
    """Whether a finding clears the severity floors and may be surfaced.

    Two floors: the plain ``min_severity`` for every finding, plus the stricter
    ``unanchored_min_severity`` for ones the engine could not anchor (a failed
    anchor is a low-confidence guess). Extracted so the on-demand replay benchmark
    asserts against the exact predicate production uses — no drift.

    ``no_line_paths`` are scan-only files (lockfiles) that a scanner hint carried
    to a lens flagged. A model finding there can never anchor, because the file is
    never in the reviewed diff, but it confirms a hint rather than guessing a
    line, so only the plain floor applies; a tool's own direct-post finding keeps
    the stricter one.
    """
    no_line = finding.path in no_line_paths and not _is_scan_finding(finding)
    return finding.severity >= cfg.min_severity and (
        finding.anchored or no_line or finding.severity >= cfg.unanchored_min_severity
    )


def _new_run(
    cfg: ReviewConfig,
    ctx: PRContext,
    provider: ProviderClient,
    state: _RunState,
    ceilings: tuple[float | None, int | None],
    *,
    can_fetch: bool,
) -> _Run:
    """The run every model call of this review shares; see :class:`_Run`.

    *ceilings* are the lens fan-out's ``(deadline_at, budget_at)`` from
    ``stages.lens_ceilings``; *can_fetch* is whether a read-only file reader
    was injected for mid-review retrieval.
    """
    # Everything a model call needs that does not vary across the run, in one
    # object: resolved here, then carried unchanged through the fan-out, the
    # oversized-batch split, and a deferral's re-run.
    deadline_at, budget_at = ceilings
    return _Run(
        model=cfg.model,
        # Constrain output to the findings schema (provider-native JSON mode) per
        # review call — NOT globally, so the reflection call keeps its own format.
        response_format=ReviewResult if cfg.structured_output else None,
        language=cfg.language,
        project=project_context(cfg.project),
        deadline_at=deadline_at,
        budget_at=budget_at,
        retrieval_budget=retrieval_budget(cfg, can_fetch=can_fetch),
        concurrency=concurrency_cap(cfg),
        cfg=cfg,
        provider=provider,
        state=state,
        changed_files=tuple(ctx.changed_files),
        stack_block=wrap_stack(_redacted_stack(ctx.stack)),
    )


def _summarise(
    ctx: PRContext,
    prepared: _PreparedReview,
    filtered: list[ReviewFinding],
    *,
    errors: list[str],
    failed_lenses: list[str],
    total_calls: int,
    failed_calls: int,
    suppressed: int,
    off_diff: int,
    reflection_skipped: str | None,
    reflection_trimmed: bool,
    risk: RiskAssessment | None,
) -> str:
    """The summary for the *filtered* findings: the summary line, every notice, the markers."""
    cfg = prepared.cfg
    state = prepared.run.state
    summary_line = _summary_line(
        len(filtered),
        cfg,
        risk,
        temperature_dropped=state.temperature_dropped,
        stack=ctx.stack,
    )
    profiler.record_returned_findings(len(filtered))

    notices = _build_notices(
        _NoticeState(
            cfg=cfg,
            capped_files=prepared.capped_files,
            total_files=prepared.total_files,
            oversized=prepared.oversized,
            skipped_by_triage=prepared.skipped_by_triage,
            errors=errors,
            total_calls=total_calls,
            failed_calls=failed_calls,
            failed_lenses=failed_lenses,
            split_batches=len(state.split_batches),
            stepped_down=sorted(state.stepped_down),
            reasoning_off=sorted(state.reasoning_off),
            escalated=dict(state.escalated),
            repaired=sorted(state.repaired),
            flooded=dict(state.flooded),
            re_asked=sorted(state.re_asked),
            schema_dropped=state.schema_dropped,
            reflection_skipped=reflection_skipped,
            suppressed=suppressed,
            off_diff=off_diff,
            open_finding_threads=ctx.open_finding_threads,
            unbatched_hints_dropped=prepared.unbatched_hints_dropped,
            self_talk=dict(state.self_talk),
            spec_unfit=prepared.spec_unfit,
            spec_reviewed=prepared.spec_reviewed,
            reflection_trimmed=reflection_trimmed,
        )
    )
    return _compose_summary(
        notices,
        summary_line,
        clean=not filtered,
        lenses=prepared.lenses,
        failed_lenses=failed_lenses,
        risk=risk,
    )


def _snap_to_diff(
    findings: list[ReviewFinding],
    ctx: PRContext,
    prepared: _PreparedReview,
    reviewed_diff: str,
) -> tuple[list[ReviewFinding], set[str]]:
    """Stage 6: re-anchor every finding, and unanchor the ones with no line to sit on.

    Returns the findings and the scan-only paths a carried hint flagged, which
    the severity floor in stage 9 exempts from its unanchored rule.
    """
    # 6. Re-anchor: the model hand-counts line numbers from the hunk header and
    #    routinely drifts a few lines. Snap each finding's line to the real
    #    changed line whose content matches its verbatim `anchor`, so comments
    #    land on the code they describe. Done before dedupe so findings the
    #    model placed on slightly different wrong lines collapse correctly.
    #    Scan findings snap against `scan_scope` (the diff the corpus saw)
    #    rather than the reviewed diff: their files can have left file_patches
    #    at triage (3c) or the file cap (3d), and dropping them as off-diff
    #    for that would blame unchanged lines that were never the problem.
    scans_at_snap = [f for f in findings if _is_scan_finding(f)]
    all_findings = _snap_findings(
        [f for f in findings if not _is_scan_finding(f)], reviewed_diff
    ) + _snap_findings(scans_at_snap, prepared.scan_scope)
    # A finding on a scan-only file (a lockfile advisory the model
    # confirmed from an unbatched hint) has no reviewed line to sit on:
    # the file is never in the reviewed diff. Mark it unanchored, so
    # every gateway demotes it to the review body rather than guessing
    # a line. Only a file a carried hint flagged is exempt from the
    # unanchored floor in step 9: a claim about a lockfile no scanner
    # flagged is a guess like any other unanchored finding.
    no_line_paths = set(ctx.scan_contents) - {path for path, _ in prepared.file_patches}
    hinted_no_line_paths = (
        {h.path for h in prepared.unbatched} & no_line_paths
        if prepared.carrier is not None
        else set()
    )
    all_findings = [
        f.model_copy(update={"anchored": False}) if f.path in no_line_paths and f.anchored else f
        for f in all_findings
    ]
    return all_findings, hinted_no_line_paths


def _scope_scans(findings: list[ReviewFinding]) -> tuple[list[ReviewFinding], int]:
    """Stage 6b: drop scan findings off the changed lines; returns how many went."""
    # 6b. Scope scan findings to the change. The tools read whole files, so
    #     they see pre-existing code the PR never touched; an anchor that
    #     matched no changed line means exactly that. The model is already
    #     told "only raise these when the diff itself shows the change" —
    #     hold the tools to the same rule, or a fixture's fake credential
    #     posts on every PR that happens to touch the file. Dependency
    #     findings are exempt: see _UNANCHORABLE_SCAN_CATEGORIES.
    scoped = [f for f in findings if f.anchored or not _is_droppable_scan(f)]
    off_diff = len(findings) - len(scoped)
    if off_diff:
        _log.info("scan findings outside the diff dropped", extra={"count": off_diff})
    return scoped, off_diff


def _suppress(
    findings: list[ReviewFinding], cfg: ReviewConfig, ctx: PRContext
) -> tuple[list[ReviewFinding], int]:
    """Stage 7: drop the findings a team marked known-fine; returns how many, per location."""
    # 7. Suppression: drop findings a team has marked known-fine — by
    #    fingerprint (cfg.ignore_fingerprints) or an inline `# lgtmaybe:
    #    ignore` pragma on/above the flagged line. Done before reflection so a
    #    suppressed finding costs no reflection tokens and never posts.
    #
    #    This and the evidence gate (7b) run BEFORE the merge (7c): dedupe
    #    keeps one finding per location, so a copy these filters would drop
    #    could otherwise win its location and take the line's real finding
    #    down with it.
    unsuppressed = apply_suppressions(findings, cfg, ctx.file_contents, ctx.feedback_downvotes)
    # Counted per location, as the merge would collapse them, so several
    # lenses' copies of one dismissed finding read as one suppression.
    unsuppressed_ids = {id(f) for f in unsuppressed}
    suppressed = len(_dedupe([f for f in findings if id(f) not in unsuppressed_ids]))
    return unsuppressed, suppressed


def _cap_scans(findings: list[ReviewFinding], off_diff: int) -> list[ReviewFinding]:
    """Stage 7a: keep the most severe ``MAX_SCAN_FINDINGS`` scan findings."""
    # 7a. Cap the scan findings after scoping (6b) and suppression (7): the
    #     cap bounds posted comments, so findings those stages drop must not
    #     spend its slots — the slot a suppressed hit vacates goes to the
    #     next on-diff hit instead. Model findings never count against it.
    scans = [f for f in findings if _is_scan_finding(f)]
    if len(scans) <= MAX_SCAN_FINDINGS:
        return findings
    considered = len(scans) + off_diff  # pre-cap count, before reassignment
    scans = sorted(scans, key=lambda f: f.severity.rank, reverse=True)[:MAX_SCAN_FINDINGS]
    _log.info(
        "scan findings capped after diff scoping",
        extra={"kept": MAX_SCAN_FINDINGS, "considered": considered},
    )
    return [f for f in findings if not _is_scan_finding(f)] + scans


def _apply_floors(
    findings: list[ReviewFinding], cfg: ReviewConfig, hinted_no_line_paths: set[str]
) -> list[ReviewFinding]:
    """Stages 9 and 9b: the severity floors, then the team's finding rules."""
    # 9. Filter: drop findings below the severity floor, and apply the
    #    stricter unanchored floor — a finding the engine could not anchor is a
    #    low-confidence guess, so surface it only when it's high/critical.
    filtered = [
        f for f in findings if _passes_severity_floor(f, cfg, no_line_paths=hinted_no_line_paths)
    ]

    # 9b. Declarative post-processing (finding_rules, default none): the
    #     team's drop / severity-remap rules, applied last so they see
    #     exactly what would otherwise post.
    if cfg.finding_rules:
        filtered = apply_finding_rules(filtered, cfg)
    return filtered


def _filter_missing_failure_scenarios(
    findings: list[ReviewFinding],
) -> list[ReviewFinding]:
    """Drop built-in defect findings that provide no concrete causal evidence."""
    kept = [
        f
        for f in findings
        if f.category not in _FAILURE_SCENARIO_CATEGORIES or (f.failure_scenario or "").strip()
    ]
    dropped = len(findings) - len(kept)
    if dropped:
        _log.info("findings missing a failure scenario dropped", extra={"count": dropped})
    return kept


def _snap_findings(findings: list[ReviewFinding], diff: str) -> list[ReviewFinding]:
    """Re-anchor each finding's ``line`` to the changed line matching its ``anchor``.

    LLMs miscount diff line numbers, so a finding's ``line`` often drifts a few
    rows off the code it describes. Each finding carries the verbatim text of the
    line it flagged; here we match that back to the real changed line (same path
    and side) and correct ``line`` to it. When the anchor is missing or matches no
    changed line, the model's ``line`` is kept untouched — never guess.
    """
    index = _prepare_candidates(changed_line_index(diff))
    return [_snap_one(f, index) for f in findings]


# Below this length an anchor is too generic for a substring match to be safe
# (`}`, `return`, `else:`), so substring matching is only tried for longer lines.
_MIN_SUBSTRING_ANCHOR = 8

# A changed line prepared for matching: (line, raw text, stripped, whitespace-normalised).
# The stripped/normalised forms are computed once per diff rather than recomputed
# for every finding that matches against the same (path, side) candidate list.
_PreparedCandidate = tuple[int, str, str, str]


def _prepare_candidates(
    index: dict[tuple[str, str], list[tuple[int, str]]],
) -> dict[tuple[str, str], list[_PreparedCandidate]]:
    """Precompute each candidate line's match forms (stripped + normalised) once."""
    return {
        key: [(line, text, text.strip(), " ".join(text.split())) for line, text in candidates]
        for key, candidates in index.items()
    }


def _match_anchor(anchor: str, candidates: list[_PreparedCandidate]) -> list[int]:
    """Line numbers of the changed lines that match *anchor*, loosest level that hits.

    Tried in order, stopping at the first non-empty level:
    1. exact (whitespace-stripped) equality;
    2. inner-whitespace-normalised equality (indentation/spacing drift);
    3. a unique substring relationship (the model trimmed a trailing comment, or
       quoted a touch more than the line) — only when exactly one line matches, so
       an ambiguous fragment never snaps to the wrong place.

    Candidates carry their stripped/normalised forms precomputed (see
    ``_prepare_candidates``), so this does no per-finding string rebuilding.
    """
    target = anchor.strip()
    exact = [line for line, _text, stripped, _norm in candidates if stripped == target]
    if exact:
        return exact
    norm = " ".join(target.split())
    normalised = [line for line, _text, _stripped, cnorm in candidates if cnorm == norm]
    if normalised:
        return normalised
    if len(target) >= _MIN_SUBSTRING_ANCHOR:
        # Both directions must clear the length floor: a trivially short
        # candidate (`)`, `pass`) is a substring of almost any anchor, so
        # without the guard it wins as the "unique" match — a confident
        # wrong-line comment.
        substring = [
            line
            for line, text, stripped, _norm in candidates
            if target in text or (len(stripped) >= _MIN_SUBSTRING_ANCHOR and stripped in target)
        ]
        if len(substring) == 1:
            return substring
    return []


def _snap_one(
    finding: ReviewFinding, index: dict[tuple[str, str], list[_PreparedCandidate]]
) -> ReviewFinding:
    if not finding.anchor or not finding.anchor.strip():
        return finding  # no anchor → trust the model's line (stays anchored)
    matches = _match_anchor(finding.anchor, index.get((finding.path, finding.side), []))
    if not matches:
        # The model quoted a line we can't find: its line number is a guess. Flag
        # it so the GitHub adapter demotes it to the summary instead of posting
        # inline on a line we can't stand behind.
        _log.info(
            "anchor unmatched — demoting finding",
            extra={"path": finding.path, "line": finding.line, "title": finding.title},
        )
        return finding.model_copy(update={"anchored": False})
    if finding.line in matches:
        return finding
    # Several identical lines can match; the model's (approximate) line is the
    # best tiebreaker — snap to the nearest match.
    best = min(matches, key=lambda line: abs(line - finding.line))
    _log.info(
        "re-anchored finding",
        extra={"path": finding.path, "from": finding.line, "to": best},
    )
    return finding.model_copy(update={"line": best})


def _dedupe(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Collapse findings at the same location (path, line, side) to a single finding.

    Multiple lenses often flag the same code with different wording — e.g. "Command
    injection via shell=True" (security) and "Unsafe shell=True call" (correctness).
    Posting both produces noisy duplicate comments; collapsing by location surfaces
    one comment per code site, which is the right level of precision.

    Trade-off: a line that genuinely has two independent issues (e.g. a null-deref
    AND a type error) will lose one of them. This is an acceptable precision/recall
    trade: line-level duplicates are far more common than two truly distinct issues
    on the exact same changed line, and the reflection pass is the right place to
    surface nuance — not duplicate inline comments.

    Selection policy (deterministic):
    1. An anchored finding beats an unanchored one: only its line is verified,
       and an unanchored winner would then fall to the unanchored severity
       floor, taking the location's real finding with it.
    2. Highest severity wins.
    3. On a tie: the finding with the longer body wins (more context is more useful).
    4. On a further tie: the first in input order wins (stable, provider-independent).

    Scan findings are keyed on their title (`tool: rule`) too, so distinct tool
    hits never merge with each other or with a model finding: osv-scanner puts
    every advisory at line 1, and a model copy that won a scanner's location
    could then be dropped by reflection — which scan findings skip — taking the
    deterministic hit with it.
    """
    best: dict[tuple[str, int, str, str | None], ReviewFinding] = {}
    for finding in findings:
        scan_title = finding.title if _is_scan_finding(finding) else None
        key = (finding.path, finding.line, finding.side, scan_title)
        existing = best.get(key)
        if existing is None:
            best[key] = finding
        elif finding.anchored != existing.anchored:
            # An unanchored finding's line is the model's guess, so it never
            # displaces an anchored one at the same key (issue #748).
            if finding.anchored:
                best[key] = finding
        elif finding.severity.rank > existing.severity.rank:
            best[key] = finding
        elif finding.severity.rank == existing.severity.rank and len(finding.body) > len(
            existing.body
        ):
            best[key] = finding
    return list(best.values())
