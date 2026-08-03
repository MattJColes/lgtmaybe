"""LLMReviewEngine: the full review pipeline.

Pipeline: redact → compress/batch → (per batch) fan out one call per review
         lens (concurrent for cloud, serial for ollama) → parse → merge/dedupe
         → require defect evidence → self-reflect/filter → filter by min_severity
         → return findings + summary.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import partial
from pathlib import Path

from lgtmaybe.core.diffparse import changed_line_index, split_by_file
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ReviewCategory,
    ReviewConfig,
    ReviewFinding,
    ReviewPreset,
    ReviewResult,
    StaticAnalysisTool,
    ToolMode,
)
from lgtmaybe.core.ports import (
    Message,
    ProviderClient,
    ProviderTruncated,
    ProviderWallTimeout,
    ReviewEngine,
)
from lgtmaybe.core.version import package_version
from lgtmaybe.github import is_reviewable

from .astgrep import SymbolResolver
from .boundaries import definition_spans
from .compress import (
    batch_files,
    context_lines_for_budget,
    count_tokens,
    expand_hunks,
    split_patch_into_hunks,
    trailing_context_lines,
)
from .directory import build_directory_block, load_context_files, rules_for
from .injection import wrap_context, wrap_diff, wrap_hints, wrap_intent
from .parse import ParseError, parse_findings, parse_needs
from .profiling import profiler
from .prompt import (
    FAST_GROUPS,
    build_correctness_block,
    build_correctness_prompt,
    build_custom_lens_block,
    build_group_block,
    build_group_prompt,
    build_lens_block,
    build_lens_prompt,
    build_shared_preamble,
    build_system_prompt,
)
from .redact import redact
from .reflect import reflect_findings
from .retrieve import MAX_FETCH_FILES, FileFetcher, resolve_needs
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

# Auto concurrency (cfg.max_concurrency=None), resolved per provider:
#
# - Cloud providers get 8. The adapter's exponential backoff absorbs a capacity
#   429 on a lower-tier account, and on bedrock cache reads don't count against
#   rate limits — so bursting the fan-out is safe, and every extra worker cuts
#   a full-latency wave off the wall clock.
# - A single ollama instance serves a model serially, so concurrent calls only
#   queue up and time out: 1.
# - openai-compatible is honest about the worst case: a llama.cpp / LM Studio
#   single-slot server wants 1, while a vLLM server batches happily at 8 —
#   default to 1 and let --max-concurrency raise it for batching servers.
_CLOUD_MAX_WORKERS = 8
_SINGLE_STREAM_PROVIDERS = frozenset({Provider.ollama, Provider.openai_compatible})
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


def _concurrency_cap(cfg: ReviewConfig) -> int:
    """How many model calls this run may have in flight: the explicit cap, else
    the provider-aware default.

    A property of the *backend*, independent of how much work there is — which
    is why it is separate from the pool size below. The oversized-batch split
    runs its pieces in a pool of its own, and needs this figure rather than the
    fan-out's: a one-lens review sizes its pool to one task, but that says
    nothing about what the provider will serve at once.
    """
    if cfg.max_concurrency is not None:
        return max(1, cfg.max_concurrency)
    if cfg.provider in _SINGLE_STREAM_PROVIDERS:
        return 1
    return _CLOUD_MAX_WORKERS


def _resolve_workers(cfg: ReviewConfig, task_count: int) -> int:
    """The fan-out pool size: the cap above, narrowed to the work there is."""
    return max(1, min(_concurrency_cap(cfg), task_count))


@dataclass(frozen=True)
class _Lens:
    """One review lens in the fan-out: a built-in category or a user-defined lens.

    Holds both prompt shapes so the fan-out is uniform — the engine no longer
    cares whether a lens came from ``ReviewCategory`` or ``extra_lenses``.
    ``system_prompt`` is the legacy monolithic shape (lens text in the system
    message, used when ``prompt_cache`` is off); ``user_block`` is the split
    (cache-shaped) layout's final user message (used when it is on — the
    default), where the system message is the shared preamble instead.
    ``carries_intent`` is true only for the built-in intent lens, the one call
    that receives the stated-intent block.
    """

    id: str
    system_prompt: str
    user_block: str
    carries_intent: bool = False
    # For a merged (fast-preset) lens: the category values the model may stamp
    # on its findings. The engine keeps a model-supplied category in this set
    # and falls back to the lens id otherwise; None (a focused lens) means the
    # lens id is always stamped — the model's value is ignored, as before.
    allowed_categories: frozenset[str] | None = None


def _build_lenses(cfg: ReviewConfig, *, has_intent: bool, retrieval: bool = False) -> list[_Lens]:
    """All lenses to run: built-ins (grouped per the preset), then user lenses.

    The fast preset runs all nine built-ins as FOUR distinct lenses — security,
    correctness, code health, artefacts — one per concern, the same set on every
    provider. The lens set is a property of the preset, not of how many workers
    happen to be available: a single-worker provider runs the same four calls,
    serially. This grouping applies only when ``categories`` is the untouched
    default: a user who explicitly listed lenses asked for exactly those, so the
    preset never regroups them. The full preset runs every category; an explicit
    list runs exactly its selected categories. Both skip intent when nothing
    states an intent.

    ``retrieval`` adds the one-round deferral ask to the legacy (``prompt_cache:
    false``) system prompts — the split shape carries it on the shared preamble
    instead, built per call. Off, every prompt here is byte-identical.
    """
    # Whether the model should still be asked for dependency-advisory claims.
    # Config-derived on purpose: keying this on whether the binary happens to be
    # installed would make the prompt — and the shared prefix cache — vary by
    # machine. A configured-but-missing finding-mode tool warns instead.
    deps = not _scanner_covers_dependency_health(cfg)
    # Likewise for committed secrets: redaction has already rewritten every
    # secret it matched to `[REDACTED]` before the diff leaves, so a scanner
    # reading the unredacted text answers this far better than the lens can.
    secrets = not _scanner_covers_secrets(cfg)
    fast = cfg.preset is ReviewPreset.fast and list(cfg.categories) == list(ReviewCategory)
    if fast:
        lenses = [
            _Lens(
                id=ReviewCategory.security.value,
                system_prompt=build_system_prompt(
                    ReviewCategory.security,
                    cfg.language,
                    secret_scanning=secrets,
                    retrieval=retrieval,
                ),
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
                system_prompt=build_correctness_prompt(has_intent, cfg.language, retrieval),
                user_block=build_correctness_block(has_intent),
                carries_intent=has_intent,
                allowed_categories=correctness_categories if has_intent else None,
            )
        )
        lenses += [
            _Lens(
                id=group.id,
                system_prompt=build_group_prompt(
                    group, cfg.language, dependency_health=deps, retrieval=retrieval
                ),
                user_block=build_group_block(group, dependency_health=deps),
                allowed_categories=frozenset(c.value for c in group.members),
            )
            for group in FAST_GROUPS
        ]
    else:
        lenses = [
            _Lens(
                id=category.value,
                system_prompt=build_system_prompt(
                    category,
                    cfg.language,
                    dependency_health=deps,
                    secret_scanning=secrets,
                    retrieval=retrieval,
                ),
                user_block=build_lens_block(
                    category, dependency_health=deps, secret_scanning=secrets
                ),
                carries_intent=category is ReviewCategory.intent,
            )
            for category in cfg.categories
        ]
        if not has_intent and any(lens.carries_intent for lens in lenses):
            lenses = [lens for lens in lenses if not lens.carries_intent]
            _log.info("intent lens skipped — no stated intent (title/description/commits)")
    lenses += [
        _Lens(
            id=lens.id,
            system_prompt=build_lens_prompt(lens, cfg.language, retrieval),
            user_block=build_custom_lens_block(lens),
        )
        for lens in cfg.extra_lenses
    ]
    return lenses


# One prepared _review_lens call, ready to submit to the fan-out pool.
_LensOutcome = tuple[list[ReviewFinding], str | None]
_ReviewTask = partial[_LensOutcome]


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
    # The message shape: True (prompt_cache on, the default) splits the prompt
    # into a cacheable shared prefix plus the lens block; False is the legacy
    # lens-in-system layout.
    split_prompt: bool
    language: str | None
    deadline_at: float | None
    budget_at: int | None
    # Mid-review retrieval budget in tokens, or None when a lens may not defer.
    retrieval_budget: int | None
    # How many calls this backend will serve at once (_concurrency_cap). Carried
    # for the oversized-batch split, which runs its pieces in a pool of its own
    # and must not out-run what the fan-out itself is allowed.
    concurrency: int


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
    context: str | None = None,
) -> list[Message]:
    """The messages for one lens call, in whichever prompt shape is configured.

    ``context`` is the fetched-for-a-deferral file text (see
    :meth:`LLMReviewEngine._review_with_context`). It rides the lens's own block —
    never the shared prefix: that prefix is the cache entry this batch's sibling
    lenses read, and one deferral must not make every one of them miss. It sits
    ahead of the lens checklist for the same reason the diff does, so the trusted
    instructions stay closest to the answer.
    """
    # Only the intent lens pays the intent-block tokens (and its injection
    # surface); the other lenses never see PR-authored prose. In the split shape
    # it rides the lens block — NOT the shared prefix — so their cached prefix
    # stays identical.
    intent = intent_block if lens.carries_intent else None
    retrieval = run.retrieval_budget is not None
    if run.split_prompt:
        # The directory block joins the ONE prefix string rather than adding
        # a fourth message: the adapter puts its cache breakpoint on the last
        # prefix block, and it varies per batch exactly like the hints do, so
        # it is warmed once by the primer and read by lenses 2..N.
        prefix = "\n\n".join(part for part in (dir_block, hint_block, wrapped) if part is not None)
        suffix = lens.user_block if context is None else f"{context}\n\n{lens.user_block}"
        if intent is not None:
            suffix = f"{intent}\n\n{suffix}"
        return [
            {"role": "system", "content": build_shared_preamble(run.language, retrieval)},
            {"role": "user", "content": prefix},
            {"role": "user", "content": suffix},
        ]

    user_content = wrapped
    if intent is not None:
        user_content = f"{intent}\n\n{user_content}"
    if hint_block is not None:
        # Static-analysis grounding: every lens sees the hints (each judges
        # relevance to its own concern) ahead of the diff they refer to.
        user_content = f"{hint_block}\n\n{user_content}"
    if dir_block is not None:
        # Directory-scoped instructions/context lead, same as in the split
        # shape, so the two layouts stay behaviourally comparable.
        user_content = f"{dir_block}\n\n{user_content}"
    if context is not None:
        user_content = f"{user_content}\n\n{context}"
    return [
        {"role": "system", "content": lens.system_prompt},
        {"role": "user", "content": user_content},
    ]


class ReviewIncompleteError(Exception):
    """Every review call failed (timeout or unparseable output) — no usable result.

    Raised instead of silently reporting a clean review, so the CLI surfaces a
    failure (non-zero exit / failure comment) rather than a false 👍 LGTM.
    """


class LLMReviewEngine(ReviewEngine):
    """Review engine that runs the full pipeline against an injected ProviderClient."""

    def __init__(
        self,
        provider: ProviderClient,
        fetch_file: FileFetcher | None = None,
        resolve_symbol: SymbolResolver | None = None,
    ) -> None:
        self._provider = provider
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
        # Batches an oversized-payload failure (wall timeout or output ceiling)
        # forced us to review in smaller pieces.
        # Per-review state (reset here, not in __init__, so a reused engine starts
        # clean); a set's add is atomic, which is all the fan-out threads need.
        self._split_batches: set[int] = set()

        # 1. Redact secrets from the diff before it leaves this process.
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
            cfg, has_intent=clean_intent is not None, retrieval=retrieval_budget is not None
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
            used_tokens = count_tokens(clean_diff)
            remaining = max(0, cfg.max_input_tokens - used_tokens)
            ctx_lines = min(cfg.context_lines, context_lines_for_budget(remaining))
            if ctx_lines > 0 and ctx.file_contents:
                after = trailing_context_lines(ctx_lines)
                file_patches = [
                    (
                        path,
                        expand_hunks(
                            patch,
                            redact(ctx.file_contents.get(path, "")),
                            ctx_lines,
                            after=after,
                            # Enclosing function/class boundaries (ast-grep; [] on
                            # any failure) so the leading pad reaches the signature.
                            boundaries=(
                                definition_spans(ctx.file_contents.get(path, ""), path)
                                if cfg.function_context and ctx.file_contents.get(path)
                                else None
                            ),
                        ),
                    )
                    for path, patch in file_patches
                ]

        with profiler.stage("batch"):
            batches = batch_files(
                file_patches, max_tokens=cfg.max_input_tokens, recursive=cfg.recursive
            )

        # 4b. Directory-scoped context files, read ONCE for the whole review from
        #     the checked-out workspace (trusted base content — never the PR
        #     head, which is why no gateway fetcher is involved). Which of them
        #     a given batch actually sees is decided per batch below.
        with profiler.stage("directory_context"):
            dir_contents = load_context_files(cfg, Path.cwd()) if cfg.directory_rules else {}

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
            split_prompt=cfg.prompt_cache,
            language=cfg.language,
            deadline_at=deadline_at,
            budget_at=budget_at,
            retrieval_budget=retrieval_budget,
            concurrency=_concurrency_cap(cfg),
        )
        workers = _resolve_workers(cfg, len(batches) * len(lenses))
        per_batch: list[tuple[bool, list[_ReviewTask]]] = []
        for batch_num, batch in enumerate(batches, start=1):
            batch_diff = "\n".join(patch for _, patch in batch)
            wrapped = wrap_diff(batch_diff)
            # Static-analysis hints for THIS batch's files only, redacted (tool
            # messages can quote hostile file content — same posture as the
            # diff) and wrapped as their own neutralised untrusted block.
            batch_paths = {path for path, _ in batch}
            batch_hints = [h for h in sa_hints if h.path in batch_paths]
            hint_block = wrap_hints(redact(format_hints(batch_hints))) if batch_hints else None
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
                wrap_intent(clean_intent, files_not_visible(ctx.changed_files, batch_paths))
                if clean_intent is not None
                else None
            )
            # Warm the prompt cache for this batch: a fully concurrent first
            # wave defeats it (every call misses, and on explicit-breakpoint
            # routes each also pays the cache write), so one lens is dispatched
            # alone and the rest of the batch releases on its completion —
            # reading the shared preamble-plus-diff prefix instead of
            # re-writing it. Gated on diff size (see _WARMUP_MIN_TOKENS) so a
            # small diff keeps full concurrency, and on a fan-out wide enough
            # for there to be a wave at all.
            warm = (
                cfg.prompt_cache
                and workers > 1
                and len(lenses) > 1
                and count_tokens(wrapped) >= _WARMUP_MIN_TOKENS
            )
            batch_tasks = [
                partial(
                    self._review_lens,
                    run,
                    wrapped,
                    intent_block,
                    hint_block,
                    dir_block,
                    batch_num,
                    lens,
                    batch,
                )
                for lens in lenses
            ]
            per_batch.append((warm, batch_tasks))

        with profiler.stage("review"):
            # Results keyed by task index and consumed in task order, so the
            # findings stay deterministic (dedupe's first-wins tiebreak is
            # order-sensitive) whatever order the futures complete in.
            for findings, error in self._fan_out(per_batch, workers):
                total_calls += 1
                if error is not None:
                    failed_calls += 1
                    errors.append(error)
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
            detail = errors[-1] if errors else "no usable output"
            raise ReviewIncompleteError(
                f"review incomplete — every review call failed ({detail}). "
                "Check the provider credentials/quota, model, and timeout "
                "(ollama: a larger model needs a longer --timeout), then retry."
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
            if interrupt_requested():
                # The process is being torn down: audit nothing, post what we
                # have. Same trade as the deadline below, on someone else's clock.
                reflection_skipped = "Review interrupted (termination signal)"
                _log.warning(
                    "review interrupted — skipping reflection",
                    extra={"findings": len(all_findings)},
                )
            elif deadline_at is not None and time.perf_counter() >= deadline_at:
                # Better unaudited findings with an honest notice than more
                # minutes past the ceiling — and never a silent quality drop.
                reflection_skipped = f"Review deadline ({cfg.max_review_seconds}s) reached"
                _log.warning(
                    "review deadline reached — skipping reflection",
                    extra={"findings": len(all_findings)},
                )
            elif budget_at is not None and profiler.total_tokens() >= budget_at:
                # Same trade, spend instead of time.
                reflection_skipped = (
                    f"Token budget (max_review_tokens = {cfg.max_review_tokens}) reached"
                )
                _log.warning(
                    "review token budget reached — skipping reflection",
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

        notices = []
        if capped_files:
            notices.append(
                f"⚠️ Reviewed the top {cfg.max_files} of {total_files} changed files "
                f"(file cap {cfg.max_files}). Raise max_files to review them all."
            )
        if skipped_by_triage:
            # Transparency: a triage skip must be visible, never silent.
            plural_files = _plural(len(skipped_by_triage))
            listed = ", ".join(f"`{p}`" for p in skipped_by_triage[:10])
            more = ", …" if len(skipped_by_triage) > 10 else ""
            notices.append(
                f"🔎 Triage skipped {len(skipped_by_triage)} low-risk file{plural_files}: "
                f"{listed}{more} (`/review full` reviews everything)."
            )
        # A budget stop is not a fault — it is the spend ceiling the user asked
        # for — so it gets its own notice naming the knob, ahead of the generic
        # incomplete notice below (which still fires, because the review IS
        # partial and that must never be softened into a clean bill of health).
        budget_skips = sum(1 for e in errors if e == _BUDGET_SKIP_REASON)
        if budget_skips:
            plural = _plural(budget_skips, " was", "s were")
            notices.append(
                f"💸 Token budget reached ({cfg.max_review_tokens} billable tokens) — "
                f"{budget_skips} of {total_calls} review call{plural} skipped, so this "
                "review is partial. Raise `max_review_tokens`, or spend less per run "
                "(a `triage_model`, fewer `categories`, lower `context_lines`)."
            )
        # Some — but not all — lenses failed: the result may be incomplete, so say
        # so and don't claim a clean bill of health. The hidden marker rides along
        # so the posting step can tell an incomplete run from a complete one
        # without matching prose the user may have restyled (summary_template).
        if failed_calls:
            detail = errors[-1] if errors else "timeout or unparseable output"
            notices.append(
                f"⚠️ {failed_calls} of {total_calls} review calls failed "
                f"({detail}); results may be incomplete.\n{INCOMPLETE_MARKER}"
            )
        # A batch that had to be shrunk is a standing signal that the diff is at
        # the edge of what this model finishes in one call — say it, or the next
        # run's failure looks like the first. Worded for both triggers (a blown
        # wall clock and a blown output ceiling), because the remedy is the same.
        if self._split_batches:
            count = len(self._split_batches)
            plural = _plural(count, many="es")
            was = _plural(count, "was", "were")
            notices.append(
                f"⏱️ {count} batch{plural} {was} too big for one "
                "call (timed out, or ran past the `max_tokens` ceiling) and "
                f"{was} reviewed in smaller pieces instead. "
                "Consider a lower `max_input_tokens`, a higher `max_tokens`, or a faster model."
            )
        if reflection_skipped:
            notices.append(
                f"⚠️ {reflection_skipped} — the self-reflection audit was "
                "skipped, so findings may include false positives."
            )
        # Findings the model DID raise and the run then hid: an ignore
        # fingerprint, an inline pragma, or a 👎 from a previous run. Reporting
        # the remaining count as a clean bill of health would let a suppression
        # quietly convert a real finding into "LGTM".
        if suppressed:
            plural = _plural(suppressed)
            notices.append(
                f"🙈 {suppressed} finding{plural} suppressed (ignored fingerprint, "
                "inline `lgtmaybe: ignore`, or a 👎 from a previous run) — not "
                "counted below."
            )
        # Deterministic hits on code this PR did not touch. Scanners read whole
        # files, so this is routine rather than a fault — but silently dropping
        # them would make a repo with a pre-existing secret look clean, so say
        # the number and where to look.
        if off_diff:
            plural = _plural(off_diff)
            notices.append(
                f"🔍 {off_diff} scan finding{plural} skipped — on unchanged lines "
                "outside this PR's diff. Run the tool over the repository to see them."
            )
        # Business this run's count cannot see: our own conversations from
        # earlier runs that nobody has resolved. An incremental run may not have
        # re-reviewed their files at all, so their absence here is no evidence
        # they were fixed.
        if ctx.open_finding_threads:
            count = ctx.open_finding_threads
            subject = _plural(count, "conversation is", "conversations are")
            notices.append(
                f"💬 {count} earlier lgtmaybe {subject} still unresolved on this PR — "
                "this run's count covers what it reviewed now, not those."
            )

        if notices:
            return filtered, "\n\n".join([*notices, summary_line])
        # A genuinely clean review (nothing flagged, every call succeeded) gets an
        # explicit thumbs-up rather than a bare "0 findings".
        if not filtered:
            return filtered, f"👍 LGTM!\n\n{summary_line}"
        return filtered, summary_line

    def _fan_out(
        self, per_batch: list[tuple[bool, list[_ReviewTask]]], workers: int
    ) -> list[_LensOutcome]:
        """Run every (batch, lens) task through one pool; results in task order.

        Batches flagged for cache warm-up submit only their FIRST lens; the
        rest of that batch is released when the primer completes (having
        written the shared prefix to the provider's prompt cache). Unflagged
        batches submit everything up front. Cross-batch work interleaves
        freely — batch 2's primer runs while batch 1's followers are in
        flight, so warming never re-serialises the whole review.
        """
        results: dict[int, _LensOutcome] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending: dict[Future[_LensOutcome], int] = {}
            primer_batch: dict[Future[_LensOutcome], int] = {}
            deferred: dict[int, list[tuple[int, _ReviewTask]]] = {}
            index = 0
            for batch_index, (warm, batch_tasks) in enumerate(per_batch):
                indexed = list(enumerate(batch_tasks, start=index))
                index += len(batch_tasks)
                if warm:
                    primer_index, primer = indexed[0]
                    future = pool.submit(primer)
                    pending[future] = primer_index
                    primer_batch[future] = batch_index
                    deferred[batch_index] = indexed[1:]
                else:
                    for task_index, task in indexed:
                        pending[pool.submit(task)] = task_index
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
                        for task_index, task in deferred.pop(batch_index, []):
                            pending[pool.submit(task)] = task_index
        return [results[i] for i in sorted(results)]

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

        ``run.split_prompt`` selects the message shape. True (``prompt_cache`` on —
        the default): shared system preamble, then the shared prefix (hints +
        diff) as one user message, then the lens block (intent + checklist +
        example) as a final user message — every lens call shares the same
        expensive prefix, which caching providers then serve from cache. False:
        the legacy shape (lens text in the system prompt, one user message),
        kept as the escape hatch for a model that reviews worse under the
        split layout.

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
            )
        )
        messages = _lens_messages(run, wrapped, intent_block, hint_block, dir_block, lens)
        return self._complete_lens(
            messages, run.model, run.response_format, batch_num, lens, on_oversized, on_needs
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
            context=wrap_context(fetched),
        )
        retry, error = self._complete_lens(
            messages, run.model, run.response_format, batch_num, lens, None, None
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
            return [], reason
        _log.warning(
            "review call was too big for one response — retrying on smaller pieces",
            extra={"lens": lens.id, "batch": batch_num, "pieces": len(pieces)},
        )

        def review_piece(piece: list[tuple[str, str]]) -> _LensOutcome:
            return self._review_lens(
                run,
                wrap_diff("\n".join(patch for _, patch in piece)),
                intent_block,
                hint_block,
                dir_block,
                batch_num,
                lens,
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
        # Width is the backend's own concurrency (never the piece count): a
        # single-stream server — ollama, a one-slot llama.cpp — serves calls
        # serially, so two at once would only queue and eat the timeout. At
        # width 1 the executor runs them in submission order, exactly as the
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
        return findings, errors[-1] if errors else None

    def _complete_lens(
        self,
        messages: list[Message],
        model: str,
        response_format: type[ReviewResult] | None,
        batch_num: int,
        lens: _Lens,
        on_oversized: Callable[[str], _LensOutcome] | None = None,
        on_needs: Callable[[list[str], list[ReviewFinding]], _LensOutcome] | None = None,
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
        """
        opts = {"response_format": response_format} if response_format is not None else {}
        # Heartbeat: log the call going out and coming back so the Action shows
        # steady per-lens progress while the model runs, not a silent gap.
        _log.info("reviewing lens", extra={"lens": lens.id})
        started = time.perf_counter()
        try:
            result = self._provider.complete(messages, model=model, **opts)
        except Exception as exc:
            reason = _error_reason(exc)
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
                    return completed, exhausted
                if on_oversized is None:
                    # Already a piece: nothing smaller to try. Report the reason
                    # rather than recurse — an unbounded cascade would spend the
                    # whole review on a model that cannot answer at any size.
                    return completed, reason
                findings, split_reason = on_oversized(reason)
                return completed + findings, split_reason
            return [], reason
        profiler.record_result(lens.id, batch_num, time.perf_counter() - started, result)
        salvaged = 0
        try:
            findings = parse_findings(result.text)
        except ParseError as exc:
            if not exc.truncated:
                _log.warning("unparseable model output", extra={"lens": lens.id})
                return [], "unparseable model output"
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
                f"ceiling — raise `max_tokens`, or lower `max_input_tokens` so each "
                f"call covers less{recovered_note}"
            )
            _log.warning(reason, extra={"lens": lens.id, "recovered": salvaged})
            # The findings fall through to be stamped like any others, but the
            # reason travels with them: the call still counts as failed, so the
            # incomplete notice fires and a partial lens is never read as a clean
            # one. Returning the salvage without it would be the silent
            # half-answer this whole path exists to prevent.
            return _stamp_categories(findings, lens), reason
        findings = _stamp_categories(findings, lens)
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

    The returned reason replaces the adapter's, whose advice ("raise
    `max_tokens`") is the one thing that provably does not work here: the cap
    does not separate thinking from answering, so raising it buys more thinking.
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
        "shrink a thinking budget; lower `reasoning_effort` instead"
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
    return [
        f.model_copy(
            update={
                "category": (
                    f.category if allowed is not None and f.category in allowed else lens.id
                )
            }
        )
        for f in findings
    ]


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


def _matches_glob(path: str, pattern: str) -> bool:
    if fnmatchcase(path, pattern):
        return True
    return pattern.startswith("**/") and fnmatchcase(path, pattern[3:])


def files_not_visible(changed_files: Sequence[str], batch_paths: set[str]) -> list[str]:
    """The PR's changed files that this batch's diff does NOT show.

    Derived, not captured. One subtraction covers every way a file goes missing
    before a lens sees it — the hardcoded generated/binary/vendored skip, the
    include/exclude globs, the ``max_files`` cap, a triage skip, an incremental
    scope, and simply being in another batch — because it asks what is left of
    the PR after this batch rather than which filter removed what.

    Capturing at each filter instead would mean five call sites kept in sync,
    three of which have no pattern list to report (the skip filter is hardcoded
    rules, the cap is a count, triage is a per-file model verdict) — and it
    would still miss batching, which has the widest reach of the six: the intent
    lens runs once PER BATCH, so on a multi-batch PR every call is judging the
    whole PR's promise against a fraction of the change.

    ``changed_files`` stays the full PR list even under incremental review (the
    incremental context replaces only ``diff``), so that case is covered for
    free — at file granularity. A file present in the increment still hides its
    *earlier* commits' changes, which this cannot express.
    """
    return sorted(set(changed_files) - batch_paths)


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


def _error_reason(exc: BaseException) -> str:
    """A concise, single-line reason for a failed review call, safe to show inline.

    Leads with the exception type (litellm names are informative — RateLimitError,
    AuthenticationError, Timeout) and collapses the message to one line, capped so
    a verbose provider error doesn't bloat the PR comment.
    """
    text = " ".join(str(exc).split())
    reason = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    return reason[:200]


def _scanner_covers_dependency_health(cfg: ReviewConfig) -> bool:
    """Whether a deterministic scanner will report dependency advisories itself.

    When one will, the lens stops being asked for them: a model's knowledge
    cutoff cannot answer "does this version have a published advisory?", so
    asking anyway only puts a confident guess beside an accurate answer.
    """
    sa = cfg.static_analysis
    tool = StaticAnalysisTool.osv_scanner
    return sa.enabled and tool in sa.tools and mode_for(tool, cfg) is ToolMode.finding


def _scanner_covers_secrets(cfg: ReviewConfig) -> bool:
    """Whether a deterministic scanner will report committed secrets itself.

    When one will, the lens stops being asked for them. Redaction rewrites every
    secret it matches to ``[REDACTED]`` before the diff is sent, so the model is
    largely being asked to find what it has been prevented from seeing — while
    gitleaks reads the unredacted head text and answers it exactly.
    """
    sa = cfg.static_analysis
    tool = StaticAnalysisTool.gitleaks
    return sa.enabled and tool in sa.tools and mode_for(tool, cfg) is ToolMode.finding


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
