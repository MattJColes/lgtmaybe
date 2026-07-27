"""LLMReviewEngine: the full review pipeline.

Pipeline: redact → compress/batch → (per batch) fan out one call per review
         lens (concurrent for cloud, serial for ollama) → parse → merge/dedupe
         → require defect evidence → self-reflect/filter → filter by min_severity
         → return findings + summary.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import partial

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
    attempts_of,
)
from lgtmaybe.core.ports import Message, ProviderClient, ProviderWallTimeout, ReviewEngine
from lgtmaybe.core.version import package_version
from lgtmaybe.github import is_reviewable

from .astgrep import SymbolResolver
from .boundaries import definition_starts
from .compress import (
    batch_files,
    context_lines_for_budget,
    count_tokens,
    expand_hunks,
    split_patch_into_hunks,
    trailing_context_lines,
)
from .injection import wrap_diff, wrap_hints, wrap_intent
from .parse import ParseError, parse_findings
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
from .retrieve import FileFetcher
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


def _resolve_workers(cfg: ReviewConfig, task_count: int) -> int:
    """The fan-out pool size: the explicit cap, else the provider-aware default."""
    if cfg.max_concurrency is not None:
        return max(1, min(cfg.max_concurrency, task_count))
    if cfg.provider in _SINGLE_STREAM_PROVIDERS:
        return 1
    return min(_CLOUD_MAX_WORKERS, task_count) or 1


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
    # A split task can keep a distinct profiler label while attributing its
    # findings to an existing public category.
    finding_category: str | None = None


def _build_lenses(cfg: ReviewConfig, *, has_intent: bool) -> list[_Lens]:
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
                    ReviewCategory.security, cfg.language, secret_scanning=secrets
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
                system_prompt=build_correctness_prompt(has_intent, cfg.language),
                user_block=build_correctness_block(has_intent),
                carries_intent=has_intent,
                allowed_categories=correctness_categories if has_intent else None,
            )
        )
        lenses += [
            _Lens(
                id=group.id,
                system_prompt=build_group_prompt(group, cfg.language, dependency_health=deps),
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
                    category, cfg.language, dependency_health=deps, secret_scanning=secrets
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
            system_prompt=build_lens_prompt(lens, cfg.language),
            user_block=build_custom_lens_block(lens),
        )
        for lens in cfg.extra_lenses
    ]
    return lenses


# One prepared _review_lens call, ready to submit to the fan-out pool.
_LensOutcome = tuple[list[ReviewFinding], str | None]
_ReviewTask = partial[_LensOutcome]


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
        # Batches a wall-clock timeout forced us to review in smaller pieces.
        # Per-review state (reset here, not in __init__, so a reused engine starts
        # clean); a set's add is atomic, which is all the fan-out threads need.
        self._split_batches: set[int] = set()

        # 1. Redact secrets from the diff before it leaves this process.
        with profiler.stage("redact"):
            clean_diff = redact(ctx.diff)

            # 1b. Stated intent (PR title/description/commit names) for the intent
            #     lens: redacted like the diff, wrapped as untrusted data, and only
            #     ever sent on the intent call. No stated intent → skip that lens
            #     rather than burn a model call judging the diff against nothing.
            intent_text = _intent_text(ctx)
            intent_block = wrap_intent(redact(intent_text)) if intent_text else None
        lenses = _build_lenses(cfg, has_intent=intent_block is not None)

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
                                definition_starts(ctx.file_contents.get(path, ""), path)
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
        # Constrain output to the findings schema (provider-native JSON mode) per
        # review call — NOT globally, so the reflection call keeps its own format.
        response_format = ReviewResult if cfg.structured_output else None
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
                    wrapped,
                    intent_block,
                    hint_block,
                    cfg.model,
                    response_format,
                    batch_num,
                    cfg.prompt_cache,
                    cfg.language,
                    deadline_at,
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
        reflection_skipped_by_deadline = False
        scanned = [f for f in all_findings if _is_scan_finding(f)]
        all_findings = [f for f in all_findings if not _is_scan_finding(f)]
        if cfg.reflect and all_findings:
            if deadline_at is not None and time.perf_counter() >= deadline_at:
                # Better unaudited findings with an honest notice than more
                # minutes past the ceiling — and never a silent quality drop.
                reflection_skipped_by_deadline = True
                _log.warning(
                    "review deadline reached — skipping reflection",
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
            plural_files = "s" if len(skipped_by_triage) != 1 else ""
            listed = ", ".join(f"`{p}`" for p in skipped_by_triage[:10])
            more = ", …" if len(skipped_by_triage) > 10 else ""
            notices.append(
                f"🔎 Triage skipped {len(skipped_by_triage)} low-risk file{plural_files}: "
                f"{listed}{more} (`/review full` reviews everything)."
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
        # the edge of what this model finishes in its budget — say it, or the next
        # run's timeout looks like the first.
        if self._split_batches:
            count = len(self._split_batches)
            plural = "es" if count != 1 else ""
            notices.append(
                f"⏱️ {count} batch{plural} timed out and {'were' if count != 1 else 'was'} "
                "reviewed in smaller pieces instead. Consider a lower `max_input_tokens` "
                "or a faster model."
            )
        if reflection_skipped_by_deadline:
            notices.append(
                f"⚠️ Review deadline ({cfg.max_review_seconds}s) reached — the "
                "self-reflection audit was skipped, so findings may include "
                "false positives."
            )
        # Findings the model DID raise and the run then hid: an ignore
        # fingerprint, an inline pragma, or a 👎 from a previous run. Reporting
        # the remaining count as a clean bill of health would let a suppression
        # quietly convert a real finding into "LGTM".
        if suppressed:
            plural = "s" if suppressed != 1 else ""
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
            plural = "s" if off_diff != 1 else ""
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
            subject = "conversation is" if count == 1 else "conversations are"
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
        wrapped: str,
        intent_block: str | None,
        hint_block: str | None,
        model: str,
        response_format: type[ReviewResult] | None,
        batch_num: int,
        split_prompt: bool,
        language: str | None,
        deadline_at: float | None,
        lens: _Lens,
        batch: list[tuple[str, str]] | None = None,
    ) -> tuple[list[ReviewFinding], str | None]:
        """Run one focused review call for a single lens (built-in or user-defined).

        Returns ``(findings, error)``. ``error`` is None on success, else a concise
        reason — the provider exception (e.g. a 429 quota error) or unparseable
        output — that the engine surfaces so a failure names its real cause instead
        of a generic "timeout". A failing lens never aborts the others.

        ``batch`` is the file patches ``wrapped`` was built from. Supplying it opts
        this call into the one retry a wall-clock timeout can actually benefit
        from: the payload is split and the pieces reviewed (see
        :meth:`_review_split`). None means "already a piece" — no further
        splitting.

        ``split_prompt`` selects the message shape. True (``prompt_cache`` on —
        the default): shared system preamble, then the shared prefix (hints +
        diff) as one user message, then the lens block (intent + checklist +
        example) as a final user message — every lens call shares the same
        expensive prefix, which caching providers then serve from cache. False:
        the legacy shape (lens text in the system prompt, one user message),
        kept as the escape hatch for a model that reviews worse under the
        split layout.
        """
        # The whole-review deadline is checked at EXECUTION time (tasks queue in
        # the pool long before a worker picks them up): past it, skip the model
        # call and surface the skip through the incomplete-results notice.
        if deadline_at is not None and time.perf_counter() >= deadline_at:
            _log.warning("review deadline reached — skipping call", extra={"lens": lens.id})
            return [], "review deadline (max_review_seconds) reached — call skipped"
        on_wall_timeout = (
            None
            if batch is None
            else partial(
                self._review_split,
                batch=batch,
                intent_block=intent_block,
                hint_block=hint_block,
                model=model,
                response_format=response_format,
                batch_num=batch_num,
                split_prompt=split_prompt,
                language=language,
                deadline_at=deadline_at,
                lens=lens,
            )
        )
        if split_prompt:
            prefix = wrapped if hint_block is None else f"{hint_block}\n\n{wrapped}"
            suffix = lens.user_block
            if lens.carries_intent and intent_block is not None:
                # Only the intent lens pays the intent-block tokens (and its
                # injection surface). It rides the lens block — NOT the shared
                # prefix — so the other lenses' cached prefix stays identical.
                suffix = f"{intent_block}\n\n{suffix}"
            messages: list[Message] = [
                {"role": "system", "content": build_shared_preamble(language)},
                {"role": "user", "content": prefix},
                {"role": "user", "content": suffix},
            ]
            return self._complete_lens(
                messages, model, response_format, batch_num, lens, on_wall_timeout
            )

        user_content = wrapped
        if lens.carries_intent and intent_block is not None:
            # Only the intent lens pays the intent-block tokens (and its
            # injection surface); the other lenses never see PR-authored prose.
            user_content = f"{intent_block}\n\n{wrapped}"
        if hint_block is not None:
            # Static-analysis grounding: every lens sees the hints (each judges
            # relevance to its own concern) ahead of the diff they refer to.
            user_content = f"{hint_block}\n\n{user_content}"
        messages = [
            {"role": "system", "content": lens.system_prompt},
            {"role": "user", "content": user_content},
        ]
        return self._complete_lens(
            messages, model, response_format, batch_num, lens, on_wall_timeout
        )

    def _review_split(
        self,
        reason: str,
        *,
        batch: list[tuple[str, str]],
        intent_block: str | None,
        hint_block: str | None,
        model: str,
        response_format: type[ReviewResult] | None,
        batch_num: int,
        split_prompt: bool,
        language: str | None,
        deadline_at: float | None,
        lens: _Lens,
    ) -> _LensOutcome:
        """Re-review a timed-out batch as smaller pieces, one call each.

        The only retry a wall-clock timeout can benefit from. Repeating the same
        request is pointless — same payload, same model, same budget — but the
        payload is the one thing we can change, and a smaller one both fits the
        budget better and gets a fresh one. Failing the lens outright instead
        would discard the whole batch's review over a size problem.

        Bounded on purpose: exactly ONE level (the pieces are reviewed with
        ``batch=None``, so a piece that times out again just fails), and the
        pieces are re-wrapped from the same redacted patches, so nothing
        unredacted or unwrapped reaches the model. Returns the original *reason*
        unchanged when the batch cannot be split — a single-hunk file has no
        smaller unit to fall back to.
        """
        pieces = _split_batch(batch)
        if len(pieces) < 2:
            return [], reason
        _log.warning(
            "review call timed out — retrying on smaller pieces",
            extra={"lens": lens.id, "batch": batch_num, "pieces": len(pieces)},
        )
        findings: list[ReviewFinding] = []
        errors: list[str] = []
        for piece in pieces:
            piece_findings, piece_error = self._review_lens(
                wrap_diff("\n".join(patch for _, patch in piece)),
                intent_block,
                hint_block,
                model,
                response_format,
                batch_num,
                split_prompt,
                language,
                deadline_at,
                lens,
            )
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
        on_wall_timeout: Callable[[str], _LensOutcome] | None = None,
    ) -> tuple[list[ReviewFinding], str | None]:
        """The provider call + parse + stamp shared by both prompt shapes.

        ``on_wall_timeout`` handles the one failure that says something about the
        *payload* rather than the provider: the call outlived its entire budget.
        It is given the failure reason and returns the outcome to use instead.
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
            profiler.record_call(
                label=lens.id,
                batch=batch_num,
                elapsed=time.perf_counter() - started,
                # What the adapter stamped on the exception: a failure that burned
                # its retry budget must not read as one that was never retried.
                # 0 only when the failure never reached the retry loop.
                attempts=attempts_of(exc),
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                error=reason,
            )
            _log.warning(
                "review call failed",
                extra={"lens": lens.id, "reason": reason},
                exc_info=True,
            )
            if isinstance(exc, ProviderWallTimeout) and on_wall_timeout is not None:
                return on_wall_timeout(reason)
            return [], reason
        profiler.record_call(
            label=lens.id,
            batch=batch_num,
            elapsed=time.perf_counter() - started,
            attempts=result.attempts,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_creation_tokens=result.cache_creation_tokens,
        )
        try:
            findings = parse_findings(result.text)
        except ParseError:
            _log.warning("unparseable model output", extra={"lens": lens.id})
            return [], "unparseable model output"
        # Stamp the originating lens (engine-derived): it drives the security
        # label and category-matched finding_rules, and surfaces in JSON output.
        # A focused lens overwrites the model's value outright; a merged
        # (fast-preset) lens covers several categories, so a model-supplied
        # value from its member set is kept and anything else falls back to
        # the lens id.
        allowed = lens.allowed_categories
        fallback_category = lens.finding_category or lens.id
        findings = [
            f.model_copy(
                update={
                    "category": (
                        f.category
                        if allowed is not None and f.category in allowed
                        else fallback_category
                    )
                }
            )
            for f in findings
        ]
        _log.info("lens reviewed", extra={"lens": lens.id, "findings": len(findings)})
        return findings, None


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
    plural = "s" if count != 1 else ""
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
    kept: list[ReviewFinding] = []
    for finding in findings:
        scenario = finding.failure_scenario
        if finding.category in _FAILURE_SCENARIO_CATEGORIES and not (scenario and scenario.strip()):
            _log.info(
                "finding missing failure scenario — dropping",
                extra={
                    "path": finding.path,
                    "line": finding.line,
                    "title": finding.title,
                    "category": finding.category,
                },
            )
            continue
        kept.append(finding)
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
