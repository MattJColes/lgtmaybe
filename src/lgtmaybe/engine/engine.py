"""LLMReviewEngine: the full review pipeline.

Pipeline: redact → compress/batch → (per batch) fan out one call per review
         lens (concurrent for cloud, serial for ollama) → parse → merge/dedupe
         → require defect evidence → self-reflect/filter → filter by min_severity
         → return findings + summary.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import partial
from pathlib import Path
from typing import Any

from lgtmaybe.core.diff import is_reviewable
from lgtmaybe.core.diffparse import changed_line_index, split_by_file
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    PRContext,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
    ReviewFinding,
    ReviewPreset,
    ReviewResult,
    StaticAnalysisTool,
    ToolMode,
    is_unrecoverable,
)
from lgtmaybe.core.ports import (
    Message,
    ProviderClient,
    ProviderTruncated,
    ProviderWallTimeout,
)
from lgtmaybe.core.version import package_version

from . import specs
from .astgrep import SymbolResolver
from .boundaries import definition_spans
from .compress import (
    batch_files,
    context_lines_for_budget,
    count_tokens,
    expand_hunks,
    set_counting_model,
    split_patch_into_hunks,
    trailing_context_lines,
)
from .directory import build_directory_block, load_context_files, rules_for
from .injection import (
    wrap_context,
    wrap_diff,
    wrap_hints,
    wrap_intent,
    wrap_not_shown,
    wrap_spec,
)
from .parse import ParseError, parse_findings, parse_needs
from .profiling import profiler
from .prompt import (
    FAST_GROUPS,
    build_correctness_block,
    build_custom_lens_block,
    build_group_block,
    build_lens_block,
    build_shared_preamble,
)
from .redact import redact
from .reflect import reflect_findings
from .repair import repair_findings
from .retrieve import MAX_FETCH_FILES, FileFetcher, resolve_needs
from .severity import clamp_to_category_ceiling
from .static_analysis import (
    SCAN_CATEGORY_PREFIX,
    UNANCHORABLE_SCAN_CATEGORIES,
    ToolFinding,
    format_hints,
    mode_for,
    partition_by_mode,
    run_static_analysis,
    tool_review_findings,
)
from .suppress import apply_suppressions
from .triage import triage_files

_log = get_logger(__name__)

# Auto concurrency (cfg.max_concurrency=None) — one number, every provider:
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
_FAILURE_SCENARIO_CATEGORIES: frozenset[str] = frozenset(
    {
        ReviewCategory.security.value,
        ReviewCategory.correctness.value,
        ReviewCategory.deprecation.value,
        ReviewCategory.performance.value,
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

# Hidden flag on the summary of a run that did not complete every lens call —
# a failed call, or one skipped past the max_review_seconds deadline (which
# reports itself as a failed call). Invisible in rendered Markdown, so it costs
# the review body nothing, and machine-readable, so the posting step can make
# the incompleteness *visible on the PR* rather than leaving it in a body edit
# nobody is notified about (see cli.run_review). Its own marker family:
# deliberately disjoint from the summary/finding/reviewed markers the GitHub
# adapter matches on.
INCOMPLETE_MARKER = "<!-- lgtmaybe-incomplete -->"

# The reason string a call skipped by the token ceiling reports. Shared with the
# summary step, which counts these to tell a budget stop (spend the user chose)
# apart from the provider failures the generic incomplete notice covers.
_BUDGET_SKIP_REASON = "token budget (max_review_tokens) reached — call skipped"

# Share of `max_review_seconds` / `max_review_tokens` held back from the lens
# fan-out so the reflection auditor still runs on a review that overruns, and the
# ceiling on the time half of that reserve.
_REFLECT_RESERVE = 0.1
_MAX_REFLECT_RESERVE_S = 300.0

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

# The reason string a call skipped by an interruption reports. Worded to name
# the cause, because the remedy differs from the deadline's: nobody should go
# hunting for a `max_review_seconds` they never hit.
_INTERRUPT_SKIP_REASON = "review interrupted (termination signal) — call skipped"

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


def _plural(n: int, one: str = "", many: str = "s") -> str:
    """The word (or suffix) for a count of *n*: *one* when it is exactly 1, else *many*."""
    return one if n == 1 else many


@dataclass(frozen=True)
class _NoticeState:
    cfg: ReviewConfig
    capped_files: bool
    total_files: int
    oversized: list[str]
    skipped_by_triage: list[str]
    errors: list[str]
    total_calls: int
    failed_calls: int
    failed_lenses: list[str]
    split_batches: int
    stepped_down: list[str]
    escalated: dict[str, str]
    repaired: list[str]
    re_asked: list[str]
    schema_dropped: bool
    reflection_skipped: str | None
    flooded: dict[str, int]
    suppressed: int
    off_diff: int
    open_finding_threads: int


def _build_notices(state: _NoticeState) -> list[str]:
    notices: list[str] = []
    cfg = state.cfg
    if state.capped_files:
        notices.append(
            f"⚠️ Reviewed the top {cfg.max_files} of {state.total_files} changed files "
            f"(file cap {cfg.max_files}). Raise max_files to review them all."
        )
    if state.oversized:
        listed = ", ".join(f"`{path}`" for path in state.oversized[:10])
        more = ", …" if len(state.oversized) > 10 else ""
        notices.append(
            f"📄 Skipped {len(state.oversized)} oversized file{_plural(len(state.oversized))} "
            f"(over {cfg.max_file_diff_lines} diff lines): {listed}{more}. "
            "Raise `max_file_diff_lines` (0 disables) to review them."
        )
    if state.skipped_by_triage:
        listed = ", ".join(f"`{path}`" for path in state.skipped_by_triage[:10])
        more = ", …" if len(state.skipped_by_triage) > 10 else ""
        notices.append(
            f"🔎 Triage skipped {len(state.skipped_by_triage)} low-risk "
            f"file{_plural(len(state.skipped_by_triage))}: {listed}{more} "
            "(`/review full` reviews everything)."
        )
    budget_skips = state.errors.count(_BUDGET_SKIP_REASON)
    if budget_skips:
        notices.append(
            f"💸 Token budget reached ({cfg.max_review_tokens} billable tokens) — "
            f"{budget_skips} of {state.total_calls} review call"
            f"{_plural(budget_skips, ' was', 's were')} skipped, so this review is partial. "
            "Raise `max_review_tokens`, or spend less per run (a `triage_model`, fewer "
            "`categories`, lower `context_lines`)."
        )
    if state.failed_calls:
        # The MOST COMMON error, not the last one. Three lenses returning prose
        # and a fourth hitting a rate limit is a schema problem, but the last
        # error names the rate limit and sends the reader to the wrong knob —
        # and a wave that fails the same way N times is exactly the shape worth
        # reporting. Identical to the old behaviour when there is one error.
        detail = (
            Counter(state.errors).most_common(1)[0][0]
            if state.errors
            else "timeout or unparseable output"
        )
        lost = ", ".join(sorted(set(state.failed_lenses)))
        which = f"{lost} — " if lost else ""
        notices.append(
            f"⚠️ {state.failed_calls} of {state.total_calls} review calls failed "
            f"({which}{detail}); results may be incomplete.\n{INCOMPLETE_MARKER}"
        )
    if state.repaired:
        count = len(state.repaired)
        listed = ", ".join(f"`{lens}`" for lens in state.repaired)
        notices.append(
            f"🔧 {count} lens{_plural(count, many='es')} returned output that did not "
            f"parse and was reformatted by a second call ({listed}). Those findings are "
            "complete — but a model that keeps doing this is not honouring the output "
            "schema, which costs an extra call every time. Check the provider log for a "
            "dropped `response_format`, or try a different model."
        )
    if state.re_asked:
        count = len(state.re_asked)
        listed = ", ".join(f"`{lens}`" for lens in state.re_asked)
        notices.append(
            f"🧷 {count} lens{_plural(count, many='es')} returned output that did not parse "
            f"under the provider's JSON schema and answered when re-asked without the schema "
            f"({listed}). Those findings are complete — but this model's structured-output "
            "mode is producing replies lgtmaybe cannot read, so every affected lens costs two "
            "wasted calls before the one that works. Set `structured_output: false` to skip "
            "them, or use a model whose JSON mode works."
        )
    if state.failed_calls and state.schema_dropped:
        notices.append(f"🧩 {_SCHEMA_DROP_NOTE}")
    if state.split_batches:
        plural = _plural(state.split_batches, many="es")
        was = _plural(state.split_batches, "was", "were")
        notices.append(
            f"⏱️ {state.split_batches} batch{plural} {was} too big for one call "
            "(timed out, or ran past the `max_tokens` ceiling) and "
            f"{was} reviewed in smaller pieces instead. Consider a lower "
            "`max_input_tokens`, a higher `max_tokens`, or a faster model."
        )
    if state.stepped_down:
        count = len(state.stepped_down)
        listed = ", ".join(f"`{lens}`" for lens in state.stepped_down)
        notices.append(
            f"🧠 {count} lens{_plural(count, many='es')} spent its whole `max_tokens` "
            f"ceiling on reasoning and was re-run once at a lower `reasoning_effort` "
            f"({listed}). Those findings come from the lower setting — lower "
            "`reasoning_effort` yourself, or raise `max_tokens`, to make that the "
            "first attempt rather than the second."
        )
    if state.escalated:
        count = len(state.escalated)
        listed = ", ".join(f"`{lens}`" for lens in sorted(state.escalated))
        models = ", ".join(f"`{model}`" for model in sorted(set(state.escalated.values())))
        notices.append(
            f"\u2934\ufe0f {count} lens{_plural(count, many='es')} could not be answered by "
            f"`{cfg.model}` and {_plural(count, 'was', 'were')} re-run on the fallback model "
            f"{models} ({listed}). Those findings are complete — but the review only finished "
            "because a second model paid for it, and a lens that keeps needing the fallback is "
            "saying it should be the primary."
        )
    if state.flooded:
        listed = ", ".join(
            f"`{lens}` ({dropped} dropped)" for lens, dropped in sorted(state.flooded.items())
        )
        notices.append(
            f"⚠️ Bounded a lens to the top {cfg.max_findings_per_lens} findings by severity: "
            f"{listed}. A lens returning many more findings than this is usually restating "
            f"one claim across many lines. Raise max_findings_per_lens to keep them all."
        )
    if state.reflection_skipped:
        notices.append(
            f"⚠️ {state.reflection_skipped} — the self-reflection audit was skipped, "
            "so findings may include false positives."
        )

    count_notices: tuple[tuple[int, Callable[[int], str]], ...] = (
        (
            state.suppressed,
            lambda count: (
                f"🙈 {count} finding{_plural(count)} suppressed (ignored fingerprint, "
                "inline `lgtmaybe: ignore`, or a 👎 from a previous run) — not counted below."
            ),
        ),
        (
            state.off_diff,
            lambda count: (
                f"🔍 {count} scan finding{_plural(count)} skipped — on unchanged "
                "lines outside this PR's diff. Run the tool over the repository to see them."
            ),
        ),
        (
            state.open_finding_threads,
            lambda count: (
                f"💬 {count} earlier lgtmaybe "
                f"{_plural(count, 'conversation is', 'conversations are')} still unresolved on "
                "this PR — this run's count covers what it reviewed now, not those."
            ),
        ),
    )
    notices.extend(render(count) for count, render in count_notices if count)
    return notices


def concurrency_cap(cfg: ReviewConfig) -> int:
    """How many model calls this run may have in flight: the explicit cap, else
    the common default — one number, not a per-provider one.

    A property of the *backend*, independent of how much work there is — which
    is why it is separate from the pool size below. The oversized-batch split
    runs its pieces in a pool of its own, and needs this figure rather than the
    fan-out's: a one-lens review sizes its pool to one task, but that says
    nothing about what the provider will serve at once.
    """
    if cfg.max_concurrency is not None:
        return max(1, cfg.max_concurrency)
    return _DEFAULT_MAX_WORKERS


def _resolve_workers(cfg: ReviewConfig, task_count: int) -> int:
    """The fan-out pool size: the cap above, narrowed to the work there is."""
    return max(1, min(concurrency_cap(cfg), task_count))


@dataclass(frozen=True)
class _Lens:
    """One review lens in the fan-out: a built-in category or a user-defined lens.

    ``user_block`` is the split layout's final user message, keeping the lens
    checklist outside the shared preamble and diff prefix.
    ``carries_intent`` is true only for the built-in intent lens, the one call
    that receives the stated-intent block; ``carries_spec`` likewise for the spec
    lens and the committed-specification block.
    """

    id: str
    user_block: str
    carries_intent: bool = False
    carries_spec: bool = False
    # For a merged (fast-preset) lens: the category values the model may stamp
    # on its findings. The engine keeps a model-supplied category in this set
    # and falls back to the lens id otherwise; None (a focused lens) means the
    # lens id is always stamped — the model's value is ignored, as before.
    allowed_categories: frozenset[str] | None = None


def _build_lenses(cfg: ReviewConfig, *, has_intent: bool, has_spec: bool = False) -> list[_Lens]:
    """All lenses to run: built-ins (grouped per the preset), then user lenses.

    The fast preset runs the nine everyday built-ins as FOUR distinct lenses —
    security, correctness, code health, artefacts — one per concern, the same set
    on every provider. The lens set is a property of the preset, not of how many
    workers happen to be available: a single-worker provider runs the same four
    calls, serially. This grouping applies only when ``categories`` is the
    untouched default: a user who explicitly listed lenses asked for exactly
    those, so the preset never regroups them. The full preset runs every
    category; an explicit list runs exactly its selected categories. Both skip
    intent when nothing states an intent, and skip spec when no committed
    specification matches the PR.

    Spec is the one built-in that does not fold into a fast-preset group. It
    carries a large block of its own (requirements, design, task list) and under
    ``fast`` the correctness lens already carries the stated intent; stacking
    both on one call degrades it. So a matched spec adds a FIFTH call — paid only
    in repositories that commit a spec the PR is delivering.

    """
    # Whether the model should still be asked for dependency-advisory claims.
    # Config-derived on purpose: keying this on whether the binary happens to be
    # installed would make the prompt — and the shared prefix cache — vary by
    # machine. A configured-but-missing finding-mode tool warns instead.
    deps = not _scanner_covers(cfg, StaticAnalysisTool.osv_scanner)
    # Likewise for committed secrets: redaction has already rewritten every
    # secret it matched to `[REDACTED]` before the diff leaves, so a scanner
    # reading the unredacted text answers this far better than the lens can.
    secrets = not _scanner_covers(cfg, StaticAnalysisTool.gitleaks)
    fast = cfg.preset is ReviewPreset.fast and list(cfg.categories) == list(ReviewCategory)
    if fast:
        lenses = [
            _Lens(
                id=ReviewCategory.security.value,
                user_block=build_lens_block(ReviewCategory.security, secret_scanning=secrets),
            ),
        ]
        correctness_categories = (
            frozenset({ReviewCategory.correctness.value, ReviewCategory.intent.value})
            if has_intent
            else frozenset({ReviewCategory.correctness.value})
        )
        lenses.append(
            _Lens(
                id=ReviewCategory.correctness.value,
                user_block=build_correctness_block(has_intent),
                carries_intent=has_intent,
                allowed_categories=correctness_categories if has_intent else None,
            )
        )
        lenses += [
            _Lens(
                id=group.id,
                user_block=build_group_block(group, dependency_health=deps),
                allowed_categories=frozenset(c.value for c in group.members),
            )
            for group in FAST_GROUPS
        ]
        if has_spec:
            lenses.append(_spec_lens())
    else:
        lenses = [
            _Lens(
                id=category.value,
                user_block=build_lens_block(
                    category, dependency_health=deps, secret_scanning=secrets
                ),
                carries_intent=category is ReviewCategory.intent,
                carries_spec=category is ReviewCategory.spec,
            )
            for category in cfg.categories
        ]
        if not has_intent and any(lens.carries_intent for lens in lenses):
            lenses = [lens for lens in lenses if not lens.carries_intent]
            _log.info("intent lens skipped — no stated intent (title/description/commits)")
        if not has_spec and any(lens.carries_spec for lens in lenses):
            lenses = [lens for lens in lenses if not lens.carries_spec]
            _log.info("spec lens skipped — no committed specification matches this PR")
    lenses += [
        _Lens(
            id=lens.id,
            user_block=build_custom_lens_block(lens),
        )
        for lens in cfg.extra_lenses
    ]
    return lenses


def _spec_lens() -> _Lens:
    """The spec lens, built the same way in either preset."""
    return _Lens(
        id=ReviewCategory.spec.value,
        user_block=build_lens_block(ReviewCategory.spec),
        carries_spec=True,
    )


# One prepared _review_lens call, ready to submit to the fan-out pool.
_LensOutcome = tuple[list[ReviewFinding], str | None]
_ReviewTask = partial[_LensOutcome]


class _RetryableReason(str):
    """A failure reason a later, identical call could still succeed at.

    The provider was briefly unavailable — a capacity 429, a 5xx, a stalled
    connection, a blown wall clock — so the same request issued once the fan-out
    has drained is worth one more go (see :meth:`LLMReviewEngine._rescue`). That
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

    The distinction exists because :meth:`LLMReviewEngine._review_split` has to
    tell two failures apart that both arrive as "the piece call failed":
    ``the smaller payload also ran out of room`` (nothing left to try) and ``a
    piece hit a capacity 429`` (the provider faltered, and the rescue wave is
    exactly what should have it). Collapsing them excluded the second.
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


@dataclass(frozen=True)
class _PreparedCall:
    """One lens call ready to submit, and the lens it speaks for.

    The lens id travels with the task so a failure can be NAMED — a failed
    security lens and a failed documentation lens read identically as a bare
    count, and they are not remotely the same news.
    """

    lens_id: str
    task: _ReviewTask


@dataclass(frozen=True)
class _Run:
    """The settings that are fixed for a whole review, resolved once in ``review()``.

    Every model call in the fan-out — the first one, an oversized batch's split
    pieces, a deferral's re-run — reads exactly the same values, so they travel
    as one object rather than as seven parameters re-declared at each hop.
    """

    model: str
    # Constrains output to the findings schema (provider-native JSON mode) on
    # review calls only — the reflection call keeps its own format.
    response_format: type[ReviewResult] | None
    language: str | None
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
    # The PR's full changed-file list. Carried for the same reason as
    # `concurrency`: a split piece shows FEWER files than the batch it came from,
    # so it has to derive its own not-shown manifest rather than inherit the
    # batch's — which would leave a piece silently unaware of its siblings' files.
    changed_files: tuple[str, ...] = ()


def _split_batch(batch: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Split a timed-out batch into smaller review units.

    Multi-file: halve the file list — the coarsest cut that halves the payload,
    and the one that keeps each file's patch intact. Single file: fall back to its
    hunks, the same unit the recursive walk uses, so an oversized lone file still
    shrinks. Returns fewer than two pieces when there is nothing smaller to try
    (an empty batch, or one file with a single hunk).
    """
    if len(batch) > 1:
        middle = len(batch) // 2
        return [batch[:middle], batch[middle:]]
    if not batch:
        return []
    path, patch = batch[0]
    hunks = split_patch_into_hunks(patch)
    if len(hunks) < 2:
        return []
    middle = len(hunks) // 2
    return [
        [(path, hunk) for hunk in hunks[:middle]],
        [(path, hunk) for hunk in hunks[middle:]],
    ]


def _skip_reason(deadline_at: float | None, budget_at: int | None, lens: _Lens) -> str | None:
    """Why this model call must not be made, or None to go ahead.

    Every ceiling is checked at EXECUTION time (tasks queue in the pool long
    before a worker picks them up, and a deferral is decided later still), so the
    figures include everything that landed in the meantime. One function, so the
    first call and a deferral's re-run can never drift apart on when to stop.
    """
    if interrupt_requested():
        _log.warning("review interrupted — skipping call", extra={"lens": lens.id})
        return _INTERRUPT_SKIP_REASON
    if deadline_at is not None and time.perf_counter() >= deadline_at:
        _log.warning("review deadline reached — skipping call", extra={"lens": lens.id})
        return "review deadline (max_review_seconds) reached — call skipped"
    if budget_at is not None and profiler.total_tokens() >= budget_at:
        _log.warning("review token budget reached — skipping call", extra={"lens": lens.id})
        return _BUDGET_SKIP_REASON
    return None


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
        part for part in (dir_block, hint_block, hidden_block, wrapped) if part is not None
    )
    suffix = lens.user_block if context is None else f"{context}\n\n{lens.user_block}"
    if spec is not None:
        suffix = f"{spec}\n\n{suffix}"
    if intent is not None:
        suffix = f"{intent}\n\n{suffix}"
    return [
        {"role": "system", "content": build_shared_preamble(run.language, retrieval)},
        {"role": "user", "content": prefix},
        {"role": "user", "content": suffix},
    ]


class ReviewIncompleteError(Exception):
    """Every review call failed (timeout or unparseable output) — no usable result.

    Raised instead of silently reporting a clean review, so the CLI surfaces a
    failure (non-zero exit / failure comment) rather than a false 👍 LGTM.
    """


class LLMReviewEngine:
    """Review engine that runs the full pipeline against an injected ProviderClient."""

    def _schema_dropped(self) -> bool:
        """Whether the adapter gave up on structured output during this run.

        Feature-detected, like ``lower_reasoning_effort``: it is an adapter-only
        method beyond the frozen ``ProviderClient`` port, so a provider that
        cannot answer simply never reports a drop rather than every fake in the
        suite growing a method to say "no".
        """
        probe = getattr(self._provider, "schema_dropped", None)
        return bool(probe()) if callable(probe) else False

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

    def review(self, ctx: PRContext, cfg: ReviewConfig) -> tuple[list[ReviewFinding], str]:
        """Run the review pipeline and return (findings, summary)."""
        # Measure the budget in the tokenizer of the model that will read it.
        # cl100k_base is OpenAI's; every batching decision on an anthropic or
        # vertex run was being made against the wrong model's token counts.
        set_counting_model(cfg.model)
        # Soft whole-review deadline: model calls reaching execution after this
        # instant are skipped (in-flight ones finish), so a pathological run
        # degrades to partial-with-a-notice instead of grinding on. Measured
        # from here so every stage — not just the model calls — counts.
        deadline_at = (
            time.perf_counter() + cfg.max_review_seconds if cfg.max_review_seconds else None
        )
        # Soft whole-review token ceiling, the spend-shaped twin of the deadline
        # above. Measured from the profiler's running total AT THIS INSTANT, not
        # from zero: the profiler is a process-wide singleton, so a second review
        # in one process must not inherit the first one's spend as its budget.
        budget_at = (
            profiler.total_tokens() + cfg.max_review_tokens if cfg.max_review_tokens else None
        )

        # Lens calls stop EARLY, leaving the tail of each ceiling for the
        # auditor. Reflection prunes a bad review, so if the lens fan-out spends
        # the budget down to zero the audit is skipped exactly when it is most
        # needed: measured, a runaway lens call consumed the whole deadline and
        # 325 unaudited findings posted, 323 of them false positives on a diff
        # with nothing wrong in it. A tenth of the budget covers one audit call
        # and is a small amount to forgo when nothing overruns. The cap bounds
        # the reserve on a long ceiling, and reserving a proportion rather than a
        # fixed number of seconds means a deliberately small ceiling does not
        # hand most of its budget to the auditor.
        lens_deadline_at = (
            deadline_at - min(cfg.max_review_seconds * _REFLECT_RESERVE, _MAX_REFLECT_RESERVE_S)
            if deadline_at is not None
            else None
        )
        lens_budget_at = (
            budget_at - int(cfg.max_review_tokens * _REFLECT_RESERVE)
            if budget_at is not None
            else None
        )
        # Batches an oversized-payload failure (wall timeout or output ceiling)
        # forced us to review in smaller pieces.
        # Per-review state (reset here, not in __init__, so a reused engine starts
        # clean); a set's add is atomic, which is all the fan-out threads need.
        self._split_batches: set[int] = set()
        # Lenses that only answered after their reasoning effort was stepped down
        # (see _retry_lower_effort). Keyed by lens so the notice counts lenses,
        # not calls — the same lens stepping down in two batches is one fact
        # about the review, not two.
        self._stepped_down: set[str] = set()
        # Lenses a SECOND model answered, keyed to the model that answered them.
        # Two paths land here and the reader cannot tell them apart, which is the
        # point: the engine escalating a truncation itself (see _escalate_model),
        # and the adapter switching model on its own for any other failure. Both
        # are read off the answering model on the result rather than recorded at
        # the call site, so neither can be disclosed and the other not. A plain
        # store needs no lock — unlike `_flooded`'s read-add-store, it is one
        # operation, and two batches racing here agree on the value anyway.
        self._escalated: dict[str, str] = {}
        # Lenses whose unparseable reply was reformatted into findings by a
        # second call (see repair.py). Keyed by lens for the same reason as
        # `_stepped_down`: one fact about the review, not one per batch.
        self._repaired: set[str] = set()
        # Written from the fan-out's worker threads, and unlike the sets above
        # a dict counter's `read, add, store` is not one atomic operation — two
        # batches' primers flooding at once would otherwise lose an update and
        # under-report the drop in the summary notice.
        self._flooded: dict[str, int] = {}
        self._flooded_lock = threading.Lock()
        # Resolved once per review so every (batch, lens) result reads the same
        # bound without threading cfg through the fan-out's every hop.
        self._max_findings_per_lens = cfg.max_findings_per_lens
        # Lenses that only parsed once the provider's JSON schema was taken off
        # the request (see _retry_without_schema). Keyed by lens for the same
        # reason as the two above.
        self._re_asked: set[str] = set()

        # 1. Redact secrets from the diff before it leaves this process.
        #    (see _schema_dropped below for the other adapter-only probe)
        with profiler.stage("redact"):
            clean_diff = redact(ctx.diff)

            # 1b. Stated intent (PR title/description/commit names) for the intent
            #     lens: redacted like the diff, and only ever sent on the intent
            #     call. No stated intent → skip that lens rather than burn a model
            #     call judging the diff against nothing.
            #
            #     Only REDACTED here; the wrapping happens per batch, because the
            #     block also has to name the files that batch cannot see.
            intent_text = _intent_text(ctx)
            clean_intent = redact(intent_text) if intent_text else None

        # 1c. The committed specification this PR is delivering, when the repo
        #     drives its work from one. Detection is a filesystem probe and
        #     selection is deterministic, so a repo with no spec — or one whose
        #     specs have nothing to do with this PR — costs a handful of stats
        #     and skips the lens entirely. Wrapped per batch like the intent,
        #     and for the same reason: the hidden-file list is batch-specific.
        with profiler.stage("spec_context"):
            clean_spec = _resolve_spec(cfg, ctx, self._workspace_root)
        # Mid-review retrieval budget, or None when a lens may not defer at all:
        # the feature is off, or nothing injected a read-only reader to fetch
        # with. One scalar rather than a config-plus-fetcher pair threaded down
        # the call chain — it answers both "may this lens defer?" and "with how
        # many tokens?". A quarter of the input budget, the same slice
        # reflection's deferral takes.
        retrieval_budget = (
            max(0, cfg.max_input_tokens // 4)
            if cfg.mid_review_retrieval and self._fetch_file is not None
            else None
        )
        lenses = _build_lenses(
            cfg,
            has_intent=clean_intent is not None,
            has_spec=clean_spec is not None,
        )

        # 2. Split into per-file patches and drop generated/binary/vendored noise,
        #    then apply the user's path filters (include_paths allowlist, then
        #    exclude_paths denylist).
        with profiler.stage("split"):
            file_patches = split_by_file(clean_diff, ctx.changed_files)
            file_patches = [
                (path, patch)
                for path, patch in file_patches
                if is_reviewable(path)
                and passes_path_filters(path, include=cfg.include_paths, exclude=cfg.exclude_paths)
            ]

            # 2b. Per-file size cap: a patch longer than max_file_diff_lines is a
            #     data blob or a generated file the name-based filter could not
            #     recognise. Drop it here — before batching, so the recursive walk
            #     never decomposes it into hundreds of per-hunk calls — and record
            #     the names, because a silent drop reads as "everything was
            #     covered". Like a lockfile skip, it never counts against max_files.
            oversized: list[str] = []
            kept: list[tuple[str, str]] = []
            for path, patch in file_patches:
                if passes_size_cap(patch, cfg.max_file_diff_lines):
                    kept.append((path, patch))
                else:
                    oversized.append(path)
            file_patches = kept

            # 3. File cap: review only the first N reviewable files, note the rest.
            total_files = len(file_patches)
            capped_files = total_files > cfg.max_files
            if capped_files:
                file_patches = file_patches[: cfg.max_files]

        # 3b. Static-analysis grounding (default off): deterministic tool
        #     findings over the reviewed files' head texts, fed to each batch's
        #     lens calls as untrusted hints to confirm or discard. Guarded here
        #     so a disabled config never touches the runner (no temp dir, no
        #     subprocess) — behaviour is byte-identical to before the feature.
        sa_hints: list[ToolFinding] = []
        sa_all: list[ToolFinding] = []
        scan_findings: list[ReviewFinding] = []
        if cfg.static_analysis.enabled and (ctx.file_contents or ctx.scan_contents):
            with profiler.stage("static_analysis"):
                reviewed_paths = {path for path, _ in file_patches}
                # Reviewed file texts, plus the scan-only dependency manifests.
                # `scan_contents` is NOT filtered by reviewed_paths: a lockfile is
                # never reviewable, so it would never survive that filter — which
                # is the whole reason it travels in its own channel.
                corpus = {p: t for p, t in ctx.file_contents.items() if p in reviewed_paths}
                corpus |= ctx.scan_contents
                sa_all = run_static_analysis(corpus, cfg)
                # Split by mode in one stable pass: run_static_analysis returns
                # findings in `sa.tools` order on purpose, and the hint block is
                # part of the cacheable prompt prefix — reordering it run to run
                # would bust the shared-prefix cache for nothing.
                sa_hints, direct = partition_by_mode(sa_all, cfg)
                scan_findings = tool_review_findings(direct, corpus)

        # 3c. Two-stage triage (default off): a cheap triage_model skips
        #     plainly-non-substantive files and ranks the rest by risk, so the
        #     strong model reviews only what deserves it. A deterministic
        #     security floor (security paths/tokens, static-analysis hits,
        #     large hunks) always escalates past triage, and any triage
        #     failure reviews everything.
        skipped_by_triage: list[str] = []
        if cfg.triage_model and file_patches:
            with profiler.stage("triage"):
                file_patches, skipped_by_triage = triage_files(
                    file_patches, sa_hints, cfg, self._provider
                )

        # 4. Pad each hunk with surrounding lines so the model sees the function
        #    and definitions around a change. The amount is budget-scaled and
        #    capped by cfg.context_lines; the pad is asymmetric — the code
        #    before a change explains it better than the code after, so the
        #    trailing side gets a quarter of the leading budget. Content is the
        #    head file text the gateway fetched (redacted), and is for
        #    understanding only — inline-comment positions are always built
        #    from the real diff.
        with profiler.stage("expand"):
            used_tokens = sum(count_tokens(patch) for _, patch in file_patches)
            remaining = max(0, cfg.max_input_tokens - used_tokens)
            ctx_lines = min(cfg.context_lines, context_lines_for_budget(remaining))
            if ctx_lines > 0 and ctx.file_contents:
                after = trailing_context_lines(ctx_lines)
                # Enclosing function/class boundaries (ast-grep; [] on any
                # failure) so the leading pad reaches the signature. Each file
                # costs its own temp dir + subprocess, and function_context is
                # on by default — run serially a max-sized PR pays ~max_files
                # spawn/teardown cycles before batching even starts. They are
                # independent, so they overlap, like static analysis's tools.
                boundaries = _definition_spans_by_path(ctx.file_contents, file_patches, cfg)
                file_patches = [
                    (
                        path,
                        expand_hunks(
                            patch,
                            redact(ctx.file_contents.get(path, "")),
                            ctx_lines,
                            after=after,
                            boundaries=boundaries.get(path),
                        ),
                    )
                    for path, patch in file_patches
                ]

        with profiler.stage("batch"):
            batches = batch_files(
                file_patches, max_tokens=_batch_budget(lenses, cfg), recursive=cfg.recursive
            )

        # 4b. Directory-scoped context files, read ONCE for the whole review from
        #     the checked-out workspace (trusted base content — never the PR
        #     head, which is why no gateway fetcher is involved). Which of them
        #     a given batch actually sees is decided per batch below.
        with profiler.stage("directory_context"):
            dir_contents = (
                load_context_files(cfg, self._workspace_root) if cfg.directory_rules else {}
            )

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
        # Everything a model call needs that does not vary across the run, in one
        # object: resolved here, then carried unchanged through the fan-out, the
        # oversized-batch split, and a deferral's re-run.
        run = _Run(
            model=cfg.model,
            # Constrain output to the findings schema (provider-native JSON mode) per
            # review call — NOT globally, so the reflection call keeps its own format.
            response_format=ReviewResult if cfg.structured_output else None,
            language=cfg.language,
            deadline_at=lens_deadline_at,
            budget_at=lens_budget_at,
            retrieval_budget=retrieval_budget,
            concurrency=concurrency_cap(cfg),
            cfg=cfg,
            changed_files=tuple(ctx.changed_files),
        )
        workers = _resolve_workers(cfg, len(batches) * len(lenses))
        per_batch: list[tuple[bool, list[_PreparedCall]]] = []
        for batch_num, batch in enumerate(batches, start=1):
            batch_diff = "\n".join(patch for _, patch in batch)
            wrapped = wrap_diff(batch_diff)
            # Static-analysis hints for THIS batch's files only, redacted (tool
            # messages can quote hostile file content — same posture as the
            # diff) and wrapped as their own neutralised untrusted block.
            batch_paths = {path for path, _ in batch}
            not_visible = files_not_visible(ctx.changed_files, batch_paths)
            batch_hints = [h for h in sa_hints if h.path in batch_paths]
            hint_block = wrap_hints(redact(format_hints(batch_hints))) if batch_hints else None
            # What this call was NOT shown, for EVERY lens — not just the two that
            # judge a whole-PR promise. Absence stated as fact beats absence
            # inferred from "code you rely on may live in files you CANNOT see",
            # which asks the model to reason about what it cannot observe. None
            # when the batch shows the whole PR, so the common case adds nothing
            # to the cached prefix.
            hidden_block = wrap_not_shown(not_visible)
            # Directory rules matching THIS batch's files, with the context
            # files they name. Batch-scoped like the hints above, so a batch
            # never pays for another directory's instructions.
            dir_block = (
                build_directory_block(rules_for(batch_paths, cfg), dir_contents)
                if cfg.directory_rules
                else None
            )
            # The intent block is built PER BATCH, not once, because it has to
            # name the files this particular call cannot see.
            intent_block = (
                wrap_intent(clean_intent, not_visible) if clean_intent is not None else None
            )
            # Same for the spec block, and the correction matters more here: a
            # requirement is delivered by CODE, so a spec call that does not know
            # which files it was denied reports every requirement implemented in
            # another batch as undelivered.
            spec_block = wrap_spec(clean_spec, not_visible) if clean_spec is not None else None
            # Warm the prompt cache for this batch: a fully concurrent first
            # wave defeats it (every call misses, and on explicit-breakpoint
            # routes each also pays the cache write), so one lens is dispatched
            # alone and the rest of the batch releases on its completion —
            # reading the shared preamble-plus-diff prefix instead of
            # re-writing it. Gated on diff size (see _WARMUP_MIN_TOKENS) so a
            # small diff keeps full concurrency, and on a fan-out wide enough
            # for there to be a wave at all.
            warm = workers > 1 and len(lenses) > 1 and count_tokens(wrapped) >= _WARMUP_MIN_TOKENS
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
                        lens,
                        batch,
                        spec_block=spec_block,
                        hidden_block=hidden_block,
                    ),
                )
                for lens in lenses
            ]
            per_batch.append((warm, batch_calls))

        with profiler.stage("review"):
            # Results keyed by task index and consumed in task order, so the
            # findings stay deterministic (dedupe's first-wins tiebreak is
            # order-sensitive) whatever order the futures complete in. Zipped
            # back against the calls in that same order so a failure can name
            # the lens it lost.
            calls = [call for _, batch_calls in per_batch for call in batch_calls]
            for call, (findings, error) in zip(
                calls, self._fan_out(per_batch, workers), strict=True
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
        if total_calls > 0 and failed_calls == total_calls and not all_findings:
            # The most common error, not the last — see _build_notices.
            detail = Counter(errors).most_common(1)[0][0] if errors else "no usable output"
            hint = f" {_SCHEMA_DROP_NOTE}" if self._schema_dropped() else ""
            raise ReviewIncompleteError(
                f"review incomplete — every review call failed ({detail}). "
                "Check the provider credentials/quota, model, and timeout "
                f"(ollama: a larger model needs a longer --timeout), then retry.{hint}"
            )

        reviewed_diff = "\n".join(patch for _, patch in file_patches)

        # 5c. Deterministic findings join the model's here — after the fan-out so
        #     the model's copy of a shared finding wins the dedupe tie below, and
        #     before snapping so they get re-anchored, deduped, suppressed and
        #     rule-filtered by exactly the same stages. Reflection is the one
        #     stage they skip; see step 8.
        all_findings = all_findings + scan_findings

        # 6. Re-anchor: the model hand-counts line numbers from the hunk header and
        #    routinely drifts a few lines. Snap each finding's line to the real
        #    changed line whose content matches its verbatim `anchor`, so comments
        #    land on the code they describe. Done before dedupe so findings the
        #    model placed on slightly different wrong lines collapse correctly.
        with profiler.stage("snap"):
            all_findings = _snap_findings(all_findings, reviewed_diff)

        # 6b. Scope scan findings to the change. The tools read whole files, so
        #     they see pre-existing code the PR never touched; an anchor that
        #     matched no changed line means exactly that. The model is already
        #     told "only raise these when the diff itself shows the change" —
        #     hold the tools to the same rule, or a fixture's fake credential
        #     posts on every PR that happens to touch the file. Dependency
        #     findings are exempt: see _UNANCHORABLE_SCAN_CATEGORIES.
        scoped = [f for f in all_findings if f.anchored or not _is_droppable_scan(f)]
        off_diff = len(all_findings) - len(scoped)
        if off_diff:
            _log.info("scan findings outside the diff dropped", extra={"count": off_diff})
        all_findings = scoped

        # 7. Merge: a finding can surface under more than one lens (a shell
        #    injection is both a security and a correctness issue), so collapse
        #    duplicates before reflecting.
        with profiler.stage("dedupe"):
            all_findings = _dedupe(all_findings)

        # 7b. Suppression: drop findings a team has marked known-fine — by
        #     fingerprint (cfg.ignore_fingerprints) or an inline `# lgtmaybe:
        #     ignore` pragma on/above the flagged line. Done before reflection so a
        #     suppressed finding costs no reflection tokens and never posts.
        with profiler.stage("suppress"):
            before_suppress = len(all_findings)
            all_findings = apply_suppressions(
                all_findings, cfg, ctx.file_contents, ctx.feedback_downvotes
            )
            suppressed = before_suppress - len(all_findings)
        if suppressed:
            _log.info("suppressed findings", extra={"count": suppressed})

        # 7c. Evidence gate: built-in defect findings must name a concrete way
        #     the changed code fails. Category is engine-stamped, so lowering the
        #     model-selected severity cannot bypass this check.
        with profiler.stage("failure_scenario"):
            all_findings = _filter_missing_failure_scenarios(all_findings)

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
        scanned = [f for f in all_findings if _is_scan_finding(f)]
        all_findings = [f for f in all_findings if not _is_scan_finding(f)]
        if cfg.reflect and all_findings:
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
                    extra={"findings": len(all_findings)},
                )
            else:
                _log.info("reflecting on findings", extra={"findings": len(all_findings)})
                # model_copy keeps file_contents — reflection now grounds itself
                # on the (redacted) head text of flagged files to verify
                # whole-file claims.
                clean_ctx = ctx.model_copy(update={"diff": reviewed_diff})
                with profiler.stage("reflect"):
                    all_findings = reflect_findings(
                        all_findings,
                        clean_ctx,
                        cfg,
                        self._provider,
                        fetch_file=self._fetch_file,
                        resolve_symbol=self._resolve_symbol,
                    )

        # 8b. Rejoin the deterministic findings the auditor never saw.
        all_findings = all_findings + scanned

        # 9. Filter: drop findings below the severity floor, and apply the
        #    stricter unanchored floor — a finding the engine could not anchor is a
        #    low-confidence guess, so surface it only when it's high/critical.
        with profiler.stage("filter"):
            filtered = [f for f in all_findings if _passes_severity_floor(f, cfg)]

            # 9b. Declarative post-processing (finding_rules, default none): the
            #     team's drop / severity-remap rules, applied last so they see
            #     exactly what would otherwise post. Imported lazily — rules.py
            #     imports this module's glob matcher, so a top import would cycle.
            if cfg.finding_rules:
                from .rules import apply_finding_rules

                filtered = apply_finding_rules(filtered, cfg)

        summary_line = _summary_line(len(filtered), cfg)
        profiler.record_returned_findings(len(filtered))

        notices = _build_notices(
            _NoticeState(
                cfg=cfg,
                capped_files=capped_files,
                total_files=total_files,
                oversized=oversized,
                skipped_by_triage=skipped_by_triage,
                errors=errors,
                total_calls=total_calls,
                failed_calls=failed_calls,
                failed_lenses=failed_lenses,
                split_batches=len(self._split_batches),
                stepped_down=sorted(self._stepped_down),
                escalated=dict(self._escalated),
                repaired=sorted(self._repaired),
                flooded=dict(self._flooded),
                re_asked=sorted(self._re_asked),
                schema_dropped=self._schema_dropped(),
                reflection_skipped=reflection_skipped,
                suppressed=suppressed,
                off_diff=off_diff,
                open_finding_threads=ctx.open_finding_threads,
            )
        )
        if notices:
            return filtered, "\n\n".join([*notices, summary_line])
        # A genuinely clean review (nothing flagged, every call succeeded) gets an
        # explicit thumbs-up rather than a bare "0 findings".
        if not filtered:
            return filtered, f"👍 LGTM!\n\n{summary_line}"
        return filtered, summary_line

    def _fan_out(
        self, per_batch: list[tuple[bool, list[_PreparedCall]]], workers: int
    ) -> list[_LensOutcome]:
        """Run every (batch, lens) call through one pool; results in call order.

        Batches flagged for cache warm-up submit only their FIRST lens; the
        rest of that batch is released when the primer completes (having
        written the shared prefix to the provider's prompt cache). Unflagged
        batches submit everything up front. Cross-batch work interleaves
        freely — batch 2's primer runs while batch 1's followers are in
        flight, so warming never re-serialises the whole review.

        Whatever failed transiently then gets one more go; see :meth:`_rescue`.
        """
        calls = [call for _, batch_calls in per_batch for call in batch_calls]
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
        return self._rescue(calls, [results[i] for i in sorted(results)])

    def _rescue(
        self, calls: list[_PreparedCall], outcomes: list[_LensOutcome]
    ) -> list[_LensOutcome]:
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
        width = min(len(retrying), _RESCUE_WORKERS)
        with ThreadPoolExecutor(max_workers=width, thread_name_prefix="lgtmaybe-rescue") as pool:
            # map() yields in submission order, so the merge stays deterministic
            # however the calls interleave.
            rescued = list(pool.map(lambda i: calls[i].task(), retrying))
        for index, (findings, error) in zip(retrying, rescued, strict=True):
            first_findings, _ = merged[index]
            merged[index] = (first_findings + findings, error)
        return merged

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
    ) -> tuple[list[ReviewFinding], str | None]:
        """Run one focused review call for a single lens (built-in or user-defined).

        Returns ``(findings, error)``. ``error`` is None on success, else a concise
        reason — the provider exception (e.g. a 429 quota error) or unparseable
        output — that the engine surfaces so a failure names its real cause instead
        of a generic "timeout". A failing lens never aborts the others.

        ``batch`` is the file patches ``wrapped`` was built from. Supplying it opts
        this call into the one retry an oversized payload can actually benefit
        from: the payload is split and the pieces reviewed (see
        :meth:`_review_split`). None means "already a piece" — no further
        splitting.

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
        skip = _skip_reason(run.deadline_at, run.budget_at, lens)
        if skip is not None:
            return [], skip
        on_oversized = (
            None
            if batch is None
            else partial(
                self._review_split,
                run=run,
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
            run.model,
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
        skip = _skip_reason(run.deadline_at, run.budget_at, lens)
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
            messages, run.model, run.response_format, batch_num, lens, None, None, run=run
        )
        return findings + retry, error

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
        pieces = _split_batch(batch)
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
            # sibling-piece files this call is now missing.
            piece_hidden = wrap_not_shown(
                files_not_visible(run.changed_files, {path for path, _ in piece})
            )
            return self._review_lens(
                run,
                wrap_diff("\n".join(patch for _, patch in piece)),
                intent_block,
                hint_block,
                dir_block,
                batch_num,
                lens,
                spec_block=spec_block,
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
            self._split_batches.add(batch_num)
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
        #   connection — says nothing about size. Its marker travels on, because
        #   one more go after the wave has drained is exactly what it needs.
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
        if any(_rescuable(e) and not isinstance(e, _PayloadReason) for e in errors):
            return findings, _RetryableReason(last)
        return findings, str(last)

    def _retry_lower_effort(
        self,
        messages: list[Message],
        model: str,
        response_format: type[ReviewResult] | None,
        batch_num: int,
        lens: _Lens,
        reason: str,
        run: _Run | None = None,
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
          same flag :meth:`_complete_lens` checks before offering a step-down —
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
        # Adapter-only, like `post_issue_comment` on the GitHub side: the effort
        # lives in provider-shaped opts (a flat `reasoning_effort`, or
        # OpenRouter's nested `reasoning` object), and the engine has no business
        # knowing which. A provider without it simply never steps down.
        if run is not None and _skip_reason(run.deadline_at, run.budget_at, lens) is not None:
            # Past a ceiling: the run is already stopping, and the notice it will
            # carry is the truncation's, not a skip's — this call never happened.
            return [], reason
        lower = getattr(self._provider, "lower_reasoning_effort", None)
        step_down = lower() if callable(lower) else None
        if not step_down:
            return [], reason
        _log.warning(
            "retrying the lens once at a lower reasoning effort",
            extra={"lens": lens.id, "batch": batch_num, "effort": step_down},
        )
        findings, error = self._complete_lens(
            messages, model, response_format, batch_num, lens, None, None, effort=step_down, run=run
        )
        if error is None:
            # Recorded on the way OUT, not the way in: the notice claims findings
            # came from the lower setting, and a retry that truncated again
            # produced no such findings. That run reports the failure it already
            # had — claiming a recovery on top of it would be two wrong notices.
            self._stepped_down.add(lens.id)
        return findings, error

    def _escalate_model(
        self,
        messages: list[Message],
        model: str,
        response_format: type[ReviewResult] | None,
        batch_num: int,
        lens: _Lens,
        reason: str,
        run: _Run | None,
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
        if run is not None and _skip_reason(run.deadline_at, run.budget_at, lens) is not None:
            return [], reason
        # Adapter-only, like `lower_reasoning_effort`: which litellm model string
        # a configured fallback resolves to is the adapter's knowledge, and a
        # provider without a second model simply never answers.
        probe = getattr(self._provider, "escalate_model", None)
        target = probe() if callable(probe) else None
        if not target:
            return [], reason
        _log.warning(
            "re-running the lens once on the fallback model",
            extra={"lens": lens.id, "batch": batch_num, "fallback_model": target},
        )
        return self._complete_lens(
            messages,
            model,
            response_format,
            batch_num,
            lens,
            None,
            None,
            escalate_to=target,
            # No unparseable recovery either: both re-runs it could start
            # (`repair_findings`, `_retry_without_schema`) go out on the primary,
            # which is the model that just failed this lens.
            recover=False,
            run=run,
        )

    def _note_answering_model(self, result: ProviderResult, lens: _Lens) -> None:
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
        asked = getattr(self._provider, "model", None)
        if answered and asked and answered != asked:
            self._escalated[lens.id] = answered

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
        if _skip_reason(run.deadline_at, run.budget_at, lens) is not None:
            return [], reason
        _log.warning(
            "re-asking the lens without the provider's JSON schema",
            extra={"lens": lens.id, "batch": batch_num},
        )
        findings, error = self._complete_lens(
            messages, model, None, batch_num, lens, None, None, recover=False, run=run
        )
        if error is None:
            # Recorded on the way OUT, like the step-down: a re-run that failed
            # too produced no findings to claim, and marking the model off the
            # back of it would disable structured output on a guess.
            self._re_asked.add(lens.id)
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

        Feature-detected and **fail-open**, like every other adapter probe: an
        adapter that cannot answer is assumed to have sent what it was given, so
        the recovery keeps working rather than silently switching itself off.
        """
        probe = getattr(self._provider, "sends_response_format", None)
        return bool(probe(model)) if callable(probe) else True

    def _drop_response_format(self, model: str) -> None:
        """Tell the adapter this model's schema mode is not working.

        Feature-detected like ``schema_dropped`` and ``lower_reasoning_effort``,
        and the first of the three that WRITES. It stays off the frozen
        ``ProviderClient`` port for the same reason they do: the knowledge is
        adapter-shaped (which litellm model string keys the drop), and a provider
        that cannot honour it should simply not remember rather than every fake
        in the suite growing a no-op.
        """
        drop = getattr(self._provider, "drop_response_format", None)
        if callable(drop):
            drop(model, "unparseable-output")

    def _stamp_and_bound(self, findings: list[ReviewFinding], lens: _Lens) -> list[ReviewFinding]:
        """Stamp the originating lens, then bound what one call may contribute.

        Both apply to every (batch, lens) result: the normal path, a repaired
        reply and a salvaged truncation. They are applied here rather than at
        each return so the three paths cannot diverge. The bound is a backstop
        against a degenerate response rather than a review budget; see
        `ReviewConfig.max_findings_per_lens`.
        """
        findings = _stamp_categories(findings, lens)
        cap = self._max_findings_per_lens
        if not cap or len(findings) <= cap:
            return findings
        # Highest severity first, input order preserved within a severity, so
        # what survives is deterministic and provider-independent. This is the
        # same selection policy `_dedupe` uses.
        kept = sorted(findings, key=lambda f: -f.severity.rank)[:cap]
        dropped = len(findings) - len(kept)
        with self._flooded_lock:
            self._flooded[lens.id] = self._flooded.get(lens.id, 0) + dropped
        _log.warning(
            "lens exceeded the per-lens finding bound — dropping the least severe",
            extra={"lens": lens.id, "returned": len(findings), "kept": cap, "dropped": dropped},
        )
        return kept

    def _complete_lens(
        self,
        messages: list[Message],
        model: str,
        response_format: type[ReviewResult] | None,
        batch_num: int,
        lens: _Lens,
        on_oversized: Callable[[str], _LensOutcome] | None = None,
        on_needs: Callable[[list[str], list[ReviewFinding]], _LensOutcome] | None = None,
        *,
        effort: dict[str, Any] | None = None,
        recover: bool = True,
        escalate_to: str | None = None,
        run: _Run | None = None,
    ) -> tuple[list[ReviewFinding], str | None]:
        """The provider call + parse + stamp shared by both prompt shapes.

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

        ``effort`` carries the per-call reasoning override of a step-down retry
        (see :meth:`_retry_lower_effort`). Not None also MARKS this call as that
        retry, which is what bounds it to one: the step-down is only ever offered
        to a call that has not already taken it.

        ``recover`` is the same idea for the unparseable path: False marks a call
        that IS a recovery, so it may not start another. Set only by
        :meth:`_retry_without_schema`, which is the second and last level.

        ``escalate_to`` names the fallback model this one call should run on. Set
        only by :meth:`_escalate_model`; it reaches the adapter as
        ``model_override``, which also stops the adapter falling back from a call
        that already is the fallback.
        """
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
            result = self._provider.complete(messages, model=model, **opts)
        except Exception as exc:
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
            if not isinstance(exc, ProviderTruncated) and not is_unrecoverable(exc):
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
            if isinstance(exc, ProviderWallTimeout | ProviderTruncated):
                # Whatever the model finished before the ceiling cut it off is real,
                # schema-valid work — kept, exactly as the parse path keeps it, so
                # the exception path is not the one place a partial answer is binned.
                completed = _salvage_truncated(exc, lens)
                exhausted = _reasoning_exhausted_reason(exc)
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
                        messages, model, response_format, batch_num, lens, exhausted, run
                    )
                    if retry_reason is None or on_oversized is None:
                        # Answered, or this is a piece/retry — see _escalate_model
                        # for why only the whole batch may buy a fallback call.
                        return completed + retried, retry_reason
                    escalated, escalate_reason = self._escalate_model(
                        messages, model, response_format, batch_num, lens, retry_reason, run
                    )
                    return completed + retried + escalated, escalate_reason
                if on_oversized is None:
                    # Already a piece: nothing smaller to try. Report the reason
                    # rather than recurse — an unbounded cascade would spend the
                    # whole review on a model that cannot answer at any size.
                    return completed, reason
                findings, split_reason = on_oversized(reason)
                if split_reason is None:
                    return completed + findings, None
                # The pieces did not cover the batch either, so the payload was
                # never the whole story. Last rung: the same request, a different
                # model. Any findings the pieces did manage ride along, and the
                # overlap with the escalation's collapses in `_dedupe`.
                escalated, escalate_reason = self._escalate_model(
                    messages, model, response_format, batch_num, lens, split_reason, run
                )
                return completed + findings + escalated, escalate_reason
            return [], reason
        elapsed = time.perf_counter() - started
        self._note_answering_model(result, lens)
        salvaged = 0
        try:
            findings = parse_findings(result.text)
        except ParseError as exc:
            if not exc.truncated:
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
                if (
                    recover
                    and run is not None
                    and _skip_reason(run.deadline_at, run.budget_at, lens) is None
                ):
                    # 1. One reformat attempt at the answer the model already
                    #    produced and was already billed for. A DIFFERENT request
                    #    — the reply plus the schema, no diff — which is why it
                    #    does not fall under the rule that an unparseable call is
                    #    never re-run: that rule is about re-issuing the identical
                    #    request at temperature 0.
                    repaired = repair_findings(
                        self._provider, run.cfg, result.text, exc.shape, lens.id
                    )
                    if repaired is not None:
                        # Complete, not partial: nothing is missing, so this must
                        # not trip the incomplete notice. Reported through its own
                        # notice instead, like a lens that stepped its reasoning
                        # effort down — successful, but worth saying out loud.
                        self._repaired.add(lens.id)
                        return self._stamp_and_bound(repaired, lens), None
                    # 2. The reformat could not do it either. If this call sent
                    #    the provider's schema, that enforcement is the remaining
                    #    suspect — re-run the lens once without it.
                    if (
                        response_format is not None
                        and run.cfg.retry_without_schema
                        and self._sends_response_format(model)
                    ):
                        retried, retry_error = self._retry_without_schema(
                            messages, model, batch_num, lens, reason, run
                        )
                        if retry_error is None:
                            return retried, None
                        return retried, reason
                return [], reason
            # A response cut off at the output ceiling is not a badly-behaved
            # model, and saying "unparseable" sends the reader looking for a
            # prompt bug instead of the ceiling they hit. The notice on the PR is
            # the only place this is ever seen, so it names which fault it was —
            # and how much of the lens survived, because "3 findings recovered"
            # and "0 recovered" are very different states to be told about.
            findings, salvaged = exc.recovered, len(exc.recovered)
            recovered_note = (
                f"; {salvaged} finding{_plural(salvaged)} completed before the cut "
                f"{_plural(salvaged, 'is', 'are')} included"
                if salvaged
                else ""
            )
            # Worded like the adapter's own ceiling error (see
            # litellm_provider._map_response): the ceiling is `max_tokens`, which
            # is usually a value the user set, not the model's own limit.
            reason = (
                f"response truncated at the {result.output_tokens}-token `max_tokens` "
                "ceiling — the batch is re-reviewed in smaller pieces automatically, so a "
                "lens that keeps doing it is usually generation instability in the model, "
                f"which a higher ceiling makes more expensive rather than prevents"
                f"{recovered_note}"
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
            # The findings fall through to be stamped like any others, but the
            # reason travels with them: the call still counts as failed, so the
            # incomplete notice fires and a partial lens is never read as a clean
            # one. Returning the salvage without it would be the silent
            # half-answer this whole path exists to prevent.
            return self._stamp_and_bound(findings, lens), reason
        findings = self._stamp_and_bound(findings, lens)
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


def _reasoning_exhausted_reason(exc: BaseException) -> str | None:
    """Why splitting this failure cannot help, or None when it still can.

    A truncation normally means the payload asked for more answer than one
    response could hold, and the remedy is to cover less. Not when the ceiling
    went on *thought*: a reasoning model draws its thinking from the same
    `max_tokens` budget, and halving the diff does not halve the thinking. The
    observed proof is a fifteen-line diff truncating at the same ceiling as a
    thousand-line one. Splitting there re-spends the whole ceiling on every piece
    and fails identically — pure added latency on a review that is already slow.

    Decided from the numbers the adapter measured (see ProviderTruncated), never
    from its message: this reads the diagnosis as data, exactly as the structured
    findings contract requires. Both counts are needed — the reasoning spend is
    meaningless without the ceiling it is a share of — so a route that reports
    neither keeps the split it has always had.

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
    if not isinstance(exc, ProviderTruncated):
        return None
    reasoning, ceiling = exc.reasoning_tokens, exc.output_tokens
    if not reasoning or not ceiling:
        return None
    if reasoning < ceiling * _REASONING_DOMINANT_SHARE:
        return None
    return (
        f"{type(exc).__name__}: spent {reasoning} of the {ceiling}-token `max_tokens` "
        "ceiling on reasoning, so the batch was not split — a smaller payload cannot "
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
    return _stamp_categories(findings, lens)


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


def _summary_line(count: int, cfg: ReviewConfig) -> str:
    """The review summary line: the user's template, or the built-in default.

    A template that fails to format (unknown placeholder, stray brace) is
    logged and falls back to the default — a cosmetic option must never fail
    a review.
    """
    version = package_version()
    if cfg.summary_template:
        try:
            return cfg.summary_template.format(
                count=count, provider=cfg.provider.value, model=cfg.model, version=version
            )
        except (KeyError, IndexError, ValueError) as exc:
            _log.warning(
                "summary_template failed to format — using the default",
                extra={"error": str(exc)},
            )
    plural = _plural(count)
    return (
        f"{count} finding{plural} · provider {cfg.provider} · model {cfg.model} "
        f"· lgtmaybe {version}"
    )


def passes_path_filters(path: str, *, include: list[str], exclude: list[str]) -> bool:
    """Whether *path* survives the config's glob filters.

    ``include`` is an allowlist (empty = everything included) and ``exclude`` a
    denylist applied after it — so an exclude always wins. Patterns are fnmatch
    globs matched against the full path, with one gitignore-style nicety: a
    ``**/``-prefixed pattern also matches at the repo root (plain fnmatch would
    demand a literal slash, silently missing ``**/*.lock`` on a root lockfile).
    """
    if include and not any(_matches_glob(path, pattern) for pattern in include):
        return False
    return not any(_matches_glob(path, pattern) for pattern in exclude)


def passes_size_cap(patch: str, max_lines: int) -> bool:
    """Whether one file's *patch* is small enough to be worth reviewing.

    The companion to the name-based skip filter, which can only recognise a
    generated file that ADMITS it in its name. A hand-named data blob — a
    154,000-line index, an 18,000-line snapshot corpus — has no such tell, so
    size is the deterministic signal left. Counting the patch's own lines (not
    the file's) keeps the judgement about what this PR actually changed.

    ``max_lines`` of 0 disables the cap.
    """
    return not max_lines or patch.count("\n") <= max_lines


def _matches_glob(path: str, pattern: str) -> bool:
    if fnmatchcase(path, pattern):
        return True
    return pattern.startswith("**/") and fnmatchcase(path, pattern[3:])


def files_not_visible(changed_files: Sequence[str], batch_paths: set[str]) -> list[str]:
    """The PR's changed files that this batch's diff does NOT show.

    Derived, not captured. One subtraction covers every way a file goes missing
    before a lens sees it — the hardcoded generated/binary/vendored skip, the
    include/exclude globs, the ``max_file_diff_lines`` size cap, the
    ``max_files`` cap, a triage skip, an incremental scope, and simply being in
    another batch — because it asks what is left of the PR after this batch
    rather than which filter removed what.

    Capturing at each filter instead would mean six call sites kept in sync,
    four of which have no pattern list to report (the skip filter is hardcoded
    rules, the caps are counts, triage is a per-file model verdict) — and it
    would still miss batching, which has the widest reach of the seven: the intent
    lens runs once PER BATCH, so on a multi-batch PR every call is judging the
    whole PR's promise against a fraction of the change.

    ``changed_files`` stays the full PR list even under incremental review (the
    incremental context replaces only ``diff``), so that case is covered for
    free — at file granularity. A file present in the increment still hides its
    *earlier* commits' changes, which this cannot express.
    """
    return sorted(set(changed_files) - batch_paths)


# Slice of the input budget the committed spec may take. The same eighth the
# directory-scoped context files get, and for the same reason: this is reference
# material handed to ONE lens per batch, not the diff under review.
_SPEC_BUDGET_DIVISOR = 8


def _resolve_spec(cfg: ReviewConfig, ctx: PRContext, root: Path) -> str | None:
    """The committed spec block for this PR, or None to skip the spec lens.

    Four deterministic steps — detect, select, read, render — and any of them
    coming up empty means no spec lens runs at all. Silence is the right answer
    far more often than not: most repositories commit no spec, and a repository
    that does may still be seeing a PR that delivers none of them.

    Spec text is read from the workspace (the trusted base branch on
    ``pull_request_target``) except for files this PR changes, which come from
    its own head text — a spec is usually committed alongside the code that
    delivers it. For the same reason the PR's changed paths join the tree
    ``detect`` walks: on the first PR of a feature the spec directory does not
    exist on the base branch at all, and detecting only what the workspace holds
    would skip the lens exactly then. Everything is redacted, including the
    ticked-task claims, which are mined from the raw diff rather than from an
    already-redacted file.
    """
    if not cfg.spec_review:
        return None
    bundles = specs.detect(root, cfg.spec_paths, ctx.changed_files)
    if not bundles:
        return None
    selected = specs.select(
        bundles,
        changed_files=ctx.changed_files,
        branch=ctx.head_branch,
        intent_text=_intent_text(ctx),
    )
    if not selected:
        return None
    contents = specs.load_spec_files(
        selected,
        root=root,
        head_texts=ctx.file_contents,
        budget_tokens=cfg.max_input_tokens // _SPEC_BUDGET_DIVISOR,
    )
    text = specs.build_spec_text(selected, contents, claims=specs.ticked_tasks(ctx.diff))
    if text is None:
        _log.info("spec lens skipped — no spec text fit the budget")
        return None
    _log.info(
        "spec lens enabled",
        extra={"specs": [b.root for b in selected], "files": sorted(contents)},
    )
    return redact(text)


def _intent_text(ctx: PRContext) -> str:
    """The PR's stated intent as one labelled text block, or "" when none is stated.

    Title + description come from a GitHub PR; commit names (first lines) come
    from either the PR's commit list or local ``git log`` — so the intent lens
    works the same on the CLI as on a PR.
    """
    parts: list[str] = []
    if ctx.title.strip():
        parts.append(f"Title: {ctx.title.strip()}")
    if ctx.description.strip():
        parts.append(f"Description:\n{ctx.description.strip()}")
    subjects = [s.strip() for s in ctx.commit_messages if s.strip()]
    if subjects:
        parts.append("Commit messages:\n" + "\n".join(f"- {s}" for s in subjects))
    return "\n\n".join(parts)


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


# Shown only alongside a failure, because on its own a dropped schema explains
# nothing — plenty of models parse fine without it. Next to "every call returned
# prose" it is very likely the cause, and it is the one part of that story the
# reader cannot see from the PR.
_SCHEMA_DROP_NOTE = (
    "The model refused the structured-output schema partway through this run, so "
    "later calls asked for JSON in the prompt only — a likely cause of the failures "
    "above. The provider log names which model and why."
)


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


def _scanner_covers(cfg: ReviewConfig, tool: StaticAnalysisTool) -> bool:
    """Whether *tool* will report its own findings on this run.

    When one will, the lens stops being asked for what it covers. For
    ``osv_scanner`` that is dependency advisories: a model's knowledge cutoff
    cannot answer "does this version have a published advisory?", so asking
    anyway only puts a confident guess beside an accurate answer. For
    ``gitleaks`` it is committed secrets: redaction rewrites every secret it
    matches to ``[REDACTED]`` before the diff is sent, so the model is largely
    being asked to find what it has been prevented from seeing — while gitleaks
    reads the unredacted head text and answers it exactly.
    """
    sa = cfg.static_analysis
    return sa.enabled and tool in sa.tools and mode_for(tool, cfg) is ToolMode.finding


_BOUNDARY_SCAN_WORKERS = 8


def _definition_spans_by_path(
    file_contents: dict[str, str],
    file_patches: Sequence[tuple[str, str]],
    cfg: ReviewConfig,
) -> dict[str, list[tuple[int, int]]]:
    """Enclosing definition spans per path, scanned concurrently.

    Empty when ``function_context`` is off, and a path with no fetched head text
    is absent — so a caller reads ``.get(path)`` and gets ``None``, exactly the
    "no boundaries" value ``expand_hunks`` took before.
    """
    if not cfg.function_context:
        return {}
    paths = [path for path, _ in file_patches if file_contents.get(path)]
    if not paths:
        return {}
    with ThreadPoolExecutor(max_workers=min(_BOUNDARY_SCAN_WORKERS, len(paths))) as pool:
        spans = pool.map(lambda path: definition_spans(file_contents[path], path), paths)
        return dict(zip(paths, spans, strict=True))


# What a call carries besides the diff: the wrapper delimiters and their
# preamble, the hint/hidden blocks when present, and the model's own message
# scaffolding. Measured once here rather than guessed per call.
_WRAPPER_OVERHEAD_TOKENS = 400


def _prompt_overhead_tokens(lenses: Sequence[_Lens], cfg: ReviewConfig) -> int:
    """Tokens every lens call spends on prompt rather than diff.

    `max_input_tokens` is the budget for the REQUEST, but only the diff was
    measured against it — so a batch packed to exactly fill the budget produced
    a request over it by the preamble plus a lens block. The reserve covers the
    widest lens, because each call carries exactly one and the batch has to fit
    whichever it is.
    """
    preamble = count_tokens(build_shared_preamble(cfg.language, cfg.mid_review_retrieval))
    widest_lens = max((count_tokens(lens.user_block) for lens in lenses), default=0)
    return preamble + widest_lens + _WRAPPER_OVERHEAD_TOKENS


def _batch_budget(lenses: Sequence[_Lens], cfg: ReviewConfig) -> int:
    """The diff budget per batch: the input budget less the prompt overhead.

    The reserve is skipped when it would take half the budget or more. A
    `max_input_tokens` that small cannot fit the prompt whatever we do, and
    reserving anyway would shrink the diff share to nothing and split every file
    into single hunks — making a misconfiguration worse rather than safer.
    """
    overhead = _prompt_overhead_tokens(lenses, cfg)
    if overhead * 2 >= cfg.max_input_tokens:
        return max(1, cfg.max_input_tokens)
    return cfg.max_input_tokens - overhead


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


def _passes_severity_floor(finding: ReviewFinding, cfg: ReviewConfig) -> bool:
    """Whether a finding clears the severity floors and may be surfaced.

    Two floors: the plain ``min_severity`` for every finding, plus the stricter
    ``unanchored_min_severity`` for ones the engine could not anchor (a failed
    anchor is a low-confidence guess). Extracted so the on-demand replay benchmark
    asserts against the exact predicate production uses — no drift.
    """
    return finding.severity >= cfg.min_severity and (
        finding.anchored or finding.severity >= cfg.unanchored_min_severity
    )


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
    1. Highest severity wins.
    2. On a tie: the finding with the longer body wins (more context is more useful).
    3. On a further tie: the first in input order wins (stable, provider-independent).
    """
    best: dict[tuple[str, int, str], ReviewFinding] = {}
    for finding in findings:
        key = (finding.path, finding.line, finding.side)
        existing = best.get(key)
        if existing is None:
            best[key] = finding
        elif finding.severity.rank > existing.severity.rank:
            best[key] = finding
        elif finding.severity.rank == existing.severity.rank and len(finding.body) > len(
            existing.body
        ):
            best[key] = finding
    return list(best.values())
