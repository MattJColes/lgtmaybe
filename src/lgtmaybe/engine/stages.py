"""The preparation stages of a review: everything before the lens fan-out.

Split out of ``LLMReviewEngine._prepare_review`` so each stage is a plain
function that can be tested, reordered or reused alone. In pipeline order:

1. :func:`lens_ceilings` — when the lens fan-out must stop (time, tokens);
2. :func:`redact_inputs` — the diff and the stated intent, redacted;
3. :func:`spec_context` — the committed specification this PR delivers;
4. :func:`filter_files` — split per file, drop noise, path filters, size cap;
5. :func:`scan_files` — static-analysis hints and direct-post findings;
6. :func:`triage_patches` — the cheap model's skips and risk ranking;
7. :func:`cap_files` — the ``max_files`` cap, after triage;
8. :func:`expand_context` — surrounding lines from the head text;
9. :func:`batch_patches` — token-budgeted batches;
10. :func:`directory_context` — directory-rule context files, read once;
11. :func:`carry_unbatched_hints` — the lens that carries scan-only hints;
12. :func:`blocks_builder` — the per-batch intent, hint, directory and spec blocks.

Each stage that the profiler times keeps its own ``profiler.stage`` block, so
``--profile`` reports exactly what it did. The orchestrator
(``LLMReviewEngine._prepare_review``) calls them in this order and arms one
model call per (batch, lens).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from fnmatch import fnmatchcase
from pathlib import Path
from typing import NamedTuple

from lgtmaybe.core.diff import is_reviewable
from lgtmaybe.core.diffparse import split_by_file
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    PRContext,
    PRStack,
    ReviewCategory,
    ReviewConfig,
    ReviewFinding,
    StackLayer,
)
from lgtmaybe.core.ports import ProviderClient

from . import specs
from .boundaries import definition_spans
from .compress import (
    batch_files,
    context_lines_for_budget,
    count_tokens,
    expand_hunks,
    trailing_context_lines,
)
from .directory import build_directory_block, load_context_files, rules_for
from .injection import wrap_hints, wrap_intent, wrap_spec
from .lenses import _Lens
from .profiling import profiler
from .prompt import build_shared_preamble, project_context
from .redact import redact
from .run import _BlocksFor
from .static_analysis import (
    ToolFinding,
    format_hints,
    partition_by_mode,
    run_static_analysis,
    tool_review_findings,
)
from .triage import triage_files

# The engine's logger; see the note in run.py.
_log = get_logger("lgtmaybe.engine.engine")

# One file's patch: (path, unified-diff text).
_Patch = tuple[str, str]

# Share of `max_review_seconds` / `max_review_tokens` held back from the lens
# fan-out so the reflection auditor still runs on a review that overruns, and the
# ceiling on the time half of that reserve.
_REFLECT_RESERVE = 0.1
_MAX_REFLECT_RESERVE_S = 300.0


def _redacted_stack(stack: PRStack | None) -> PRStack | None:
    """*stack* with every title and path redacted, like the diff, before egress."""
    if stack is None:
        return None

    def clean(layers: list[StackLayer]) -> list[StackLayer]:
        return [
            layer.model_copy(
                update={
                    "title": redact(layer.title),
                    "files": [redact(path) for path in layer.files],
                }
            )
            for layer in layers
        ]

    return stack.model_copy(
        update={
            "base_ref": redact(stack.base_ref),
            "below": clean(stack.below),
            "above": clean(stack.above),
        }
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


class _SpecContext(NamedTuple):
    """What spec resolution decided: the block to send, and what it could not."""

    #: The redacted spec block, or None to skip the spec lens.
    text: str | None
    #: Roots of the specs that matched this PR but none of whose files could fit
    #: the budget on their own. A matched spec that goes unsent is a lens the
    #: round should have run and did not, which the summary says out loud —
    #: unlike the ordinary "nothing matched" skip, which is silent by design. A
    #: spec that lost the budget to a higher-ranked one is not here: it could
    #: have fit, so the notice's advice would be wrong for it.
    unfit: tuple[str, ...]


def _resolve_spec(cfg: ReviewConfig, ctx: PRContext, root: Path) -> _SpecContext:
    """The committed spec block for this PR, or a skip — with what did not fit.

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
    skipped = _SpecContext(None, ())
    if not cfg.spec_review:
        return skipped
    bundles = specs.detect(root, cfg.spec_paths, ctx.changed_files)
    if not bundles:
        return skipped
    selected = specs.select(
        bundles,
        changed_files=ctx.changed_files,
        branch=ctx.head_branch,
        intent_text=_intent_text(ctx),
    )
    if not selected:
        return skipped
    budget = cfg.max_input_tokens // _SPEC_BUDGET_DIVISOR
    contents = specs.load_spec_files(
        selected,
        root=root,
        head_texts=ctx.file_contents,
        budget_tokens=budget,
    )
    unfit = specs.unfit_bundles(
        selected, contents, root=root, head_texts=ctx.file_contents, budget_tokens=budget
    )
    if unfit:
        _log.warning(
            "matched spec did not fit the spec budget",
            extra={"specs": list(unfit), "budget_tokens": budget},
        )
    text = specs.build_spec_text(selected, contents, claims=specs.ticked_tasks(ctx.diff))
    if text is None:
        _log.info("spec lens skipped — no spec text fit the budget")
        return _SpecContext(None, unfit)
    _log.info(
        "spec lens enabled",
        extra={"specs": [b.root for b in selected], "files": sorted(contents)},
    )
    return _SpecContext(redact(text), unfit)


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
    preamble = count_tokens(
        build_shared_preamble(cfg.language, cfg.mid_review_retrieval, project_context(cfg.project))
    )
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


def _unbatched_hint_carrier(lenses: Sequence[_Lens]) -> _Lens | None:
    """The lens that carries hints no batch holds: security, else correctness."""
    by_id = {lens.id: lens for lens in lenses}
    return by_id.get(ReviewCategory.security.value) or by_id.get(ReviewCategory.correctness.value)


_UNBATCHED_HINTS_LEAD = (
    "The static-analysis hints below are about files the diff does not show: "
    "dependency lockfiles and manifests, which are never reviewed line by line. "
    "Confirm or discard each one like any other hint. Report a confirmed advisory "
    "against the lockfile path the hint names (for example `uv.lock`), quoting the "
    "affected package and version as the `anchor`."
)


def _unbatched_hint_block(hints: Sequence[ToolFinding]) -> str:
    """The trusted lead-in plus the redacted, wrapped hints for the carrier lens."""
    return f"{_UNBATCHED_HINTS_LEAD}\n\n{wrap_hints(redact(format_hints(list(hints))))}"


def lens_ceilings(cfg: ReviewConfig) -> tuple[float | None, int | None]:
    """When the lens fan-out must stop: ``(deadline_at, budget_at)``, None when off.

    The deadline is a ``time.perf_counter()`` instant, measured from this call,
    so the orchestrator makes it first; the budget is a token count on the
    run's own tally. Both hold back a reserve for the reflection auditor.
    """
    # Soft whole-review deadline: model calls reaching execution after this
    # instant are skipped (in-flight ones finish), so a pathological run
    # degrades to partial-with-a-notice instead of grinding on. Measured
    # from here so every stage — not just the model calls — counts.
    deadline_at = time.perf_counter() + cfg.max_review_seconds if cfg.max_review_seconds else None
    # Soft whole-review token ceiling, the spend-shaped twin of the deadline
    # above. Counted on this run's own tally, from zero: every model call
    # this review makes goes through `provider`, which charges it to
    # `state`, so neither an earlier review in this process nor one running
    # beside it on the same engine is charged against this budget.
    budget_at = cfg.max_review_tokens or None

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
        budget_at - int(cfg.max_review_tokens * _REFLECT_RESERVE) if budget_at is not None else None
    )
    return lens_deadline_at, lens_budget_at


class RedactedInputs(NamedTuple):
    """The diff and the stated intent, redacted before either leaves the process."""

    diff: str
    #: The redacted stated intent, or None when the PR states none.
    intent: str | None


def redact_inputs(ctx: PRContext) -> RedactedInputs:
    """Stage 1: redact secrets from the diff, and from the stated intent."""
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
    return RedactedInputs(clean_diff, clean_intent)


def spec_context(cfg: ReviewConfig, ctx: PRContext, root: Path) -> _SpecContext:
    """Stage 1c: the committed specification this PR is delivering, if any."""
    # 1c. The committed specification this PR is delivering, when the repo
    #     drives its work from one. Detection is a filesystem probe and
    #     selection is deterministic, so a repo with no spec — or one whose
    #     specs have nothing to do with this PR — costs a handful of stats
    #     and skips the lens entirely. Wrapped per batch like the intent,
    #     and for the same reason: the hidden-file list is batch-specific.
    with profiler.stage("spec_context"):
        return _resolve_spec(cfg, ctx, root)


def retrieval_budget(cfg: ReviewConfig, *, can_fetch: bool) -> int | None:
    """Mid-review retrieval budget in tokens, or None when a lens may not defer.

    *can_fetch* is whether the engine was given a read-only file reader.
    """
    # Mid-review retrieval budget, or None when a lens may not defer at all:
    # the feature is off, or nothing injected a read-only reader to fetch
    # with. One scalar rather than a config-plus-fetcher pair threaded down
    # the call chain — it answers both "may this lens defer?" and "with how
    # many tokens?". A quarter of the input budget, the same slice
    # reflection's deferral takes.
    return max(0, cfg.max_input_tokens // 4) if cfg.mid_review_retrieval and can_fetch else None


class FilteredFiles(NamedTuple):
    """The reviewable per-file patches, and the files the size cap dropped."""

    patches: list[_Patch]
    #: Paths over ``max_file_diff_lines``, named in the summary.
    oversized: list[str]


def filter_files(diff: str, ctx: PRContext, cfg: ReviewConfig) -> FilteredFiles:
    """Stage 2: split *diff* per file, drop noise, apply path filters and the size cap."""
    # 2. Split into per-file patches and drop generated/binary/vendored noise,
    #    then apply the user's path filters (include_paths allowlist, then
    #    exclude_paths denylist).
    with profiler.stage("split"):
        file_patches = split_by_file(diff, ctx.changed_files)
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
    return FilteredFiles(kept, oversized)


class ScanOutcome(NamedTuple):
    """What the static-analysis tools found, split by how each tool posts."""

    #: Hint-mode findings, fed to the lens calls as untrusted hints.
    hints: list[ToolFinding]
    #: Every tool finding, hint-mode or finding-mode: triage's security floor.
    tool_findings: list[ToolFinding]
    #: Finding-mode findings, posted without the model.
    findings: list[ReviewFinding]
    #: The diff exactly as the scan corpus saw it; scan findings snap against it.
    scope: str


def scan_files(file_patches: Sequence[_Patch], ctx: PRContext, cfg: ReviewConfig) -> ScanOutcome:
    """Stage 3b: static-analysis grounding over the reviewed files' head texts."""
    # 3b. Static-analysis grounding (default off): deterministic tool
    #     findings over the reviewed files' head texts, fed to each batch's
    #     lens calls as untrusted hints to confirm or discard. Guarded here
    #     so a disabled config never touches the runner (no temp dir, no
    #     subprocess) — behaviour is byte-identical to before the feature.
    sa_hints: list[ToolFinding] = []
    sa_all: list[ToolFinding] = []
    scan_findings: list[ReviewFinding] = []
    # The diff exactly as the scan corpus saw it: triage (3c) and the file
    # cap (3d) narrow file_patches after this, and a scan finding must snap
    # against what was actually scanned, not the narrower reviewed diff.
    scan_scope = ""
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
            scan_scope = "\n".join(patch for _, patch in file_patches)
    return ScanOutcome(sa_hints, sa_all, scan_findings, scan_scope)


def triage_patches(
    file_patches: list[_Patch],
    tool_findings: list[ToolFinding],
    cfg: ReviewConfig,
    provider: ProviderClient,
) -> tuple[list[_Patch], list[str]]:
    """Stage 3c: the files triage keeps (ranked by risk), and the paths it skipped."""
    # 3c. Two-stage triage (default off): a cheap triage_model skips
    #     plainly-non-substantive files and ranks the rest by risk, so the
    #     strong model reviews only what deserves it. A deterministic
    #     security floor (security paths/tokens, static-analysis hits,
    #     large hunks) always escalates past triage, and any triage
    #     failure reviews everything. Every static-analysis finding feeds
    #     the floor, hint-mode or finding-mode: a direct-post hit must stop
    #     triage skipping its file, or the diff scoping drops the hit.
    skipped_by_triage: list[str] = []
    if cfg.triage_model and file_patches:
        with profiler.stage("triage"):
            file_patches, skipped_by_triage = triage_files(
                file_patches, tool_findings, cfg, provider
            )
    return file_patches, skipped_by_triage


def cap_files(file_patches: list[_Patch], cfg: ReviewConfig) -> tuple[list[_Patch], bool]:
    """Stage 3d: the first ``max_files`` patches, and whether the cap cut any."""
    # 3d. File cap: after triage so it drops the lowest-risk survivors,
    # rather than whichever paths happened to appear last in the diff.
    capped_files = len(file_patches) > cfg.max_files
    if capped_files:
        file_patches = file_patches[: cfg.max_files]
    return file_patches, capped_files


def expand_context(file_patches: list[_Patch], ctx: PRContext, cfg: ReviewConfig) -> list[_Patch]:
    """Stage 4: pad each hunk with surrounding head-text lines, within the budget."""
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
    return file_patches


def batch_patches(
    file_patches: list[_Patch], lenses: Sequence[_Lens], cfg: ReviewConfig
) -> list[list[_Patch]]:
    """Stage 4a: pack the patches into batches that fit one lens call's budget."""
    with profiler.stage("batch"):
        return batch_files(
            file_patches, max_tokens=_batch_budget(lenses, cfg), recursive=cfg.recursive
        )


def directory_context(cfg: ReviewConfig, root: Path) -> dict[str, str]:
    """Stage 4b: the directory-rule context files, read once for the whole review."""
    # 4b. Directory-scoped context files, read ONCE for the whole review from
    #     the checked-out workspace (trusted base content — never the PR
    #     head, which is why no gateway fetcher is involved). Which of them
    #     a given batch actually sees is decided per batch.
    with profiler.stage("directory_context"):
        return load_context_files(cfg, root) if cfg.directory_rules else {}


class UnbatchedHints(NamedTuple):
    """Static-analysis hints on files no batch holds, and the lens carrying them."""

    hints: list[ToolFinding]
    #: The lens chosen to carry them, as built (None: no lens can).
    carrier: _Lens | None
    #: *carrier* with the hints prepended to its own uncached block.
    carrier_lens: _Lens | None
    #: How many hints no lens could carry; named in the summary.
    dropped: int


def carry_unbatched_hints(
    hints: Sequence[ToolFinding],
    scan_contents: dict[str, str],
    batches: Sequence[Sequence[_Patch]],
    lenses: Sequence[_Lens],
) -> UnbatchedHints:
    """Stage 4c: route hints on scan-only files to the one lens call that reads them."""
    # Hints on scan-only files (dependency lockfiles and manifests the review
    # never reads line by line) have no batch to join, so the per-batch
    # filter would lose them. They ride ONE lens call instead: the
    # first batch's security lens (correctness when the run has no security
    # lens), on that lens's own uncached block, so the shared prefix every
    # sibling lens reads from cache stays byte-identical.
    batched_paths = {path for batch in batches for path, _ in batch}
    unbatched = [h for h in hints if h.path in scan_contents and h.path not in batched_paths]
    carrier = _unbatched_hint_carrier(lenses) if unbatched and batches else None
    unbatched_dropped = len(unbatched) if carrier is None else 0
    if unbatched_dropped:
        _log.info(
            "unbatched static-analysis hints had no lens to carry them",
            extra={"count": unbatched_dropped},
        )
    carrier_lens = (
        replace(
            carrier,
            user_block=f"{_unbatched_hint_block(unbatched)}\n\n{carrier.user_block}",
        )
        if carrier is not None
        else None
    )
    return UnbatchedHints(unbatched, carrier, carrier_lens, unbatched_dropped)


def blocks_builder(
    ctx: PRContext,
    cfg: ReviewConfig,
    hints: Sequence[ToolFinding],
    intent: str | None,
    spec: str | None,
    dir_contents: dict[str, str],
) -> _BlocksFor:
    """Stage 4d: the builder of a call's intent, hint, directory and spec blocks.

    *intent* and *spec* are the redacted texts (None skips the block).
    """

    # Blocks for any SUBSET of a batch's files. A split piece shows a
    # subset, so the batch's own intent, spec, hint and directory blocks
    # are wrong for it twice over: their not-visible lists omit the
    # sibling-piece files, and their hints and rules cover files the piece
    # cannot see. One builder serves the batch and each piece, so the two
    # can never drift.
    def blocks_for(paths: set[str]) -> tuple[str | None, str | None, str | None, str | None]:
        not_visible = files_not_visible(ctx.changed_files, paths)
        batch_hints = [h for h in hints if h.path in paths]
        return (
            wrap_intent(intent, not_visible) if intent is not None else None,
            wrap_hints(redact(format_hints(batch_hints))) if batch_hints else None,
            build_directory_block(rules_for(paths, cfg), dir_contents)
            if cfg.directory_rules
            else None,
            wrap_spec(spec, not_visible) if spec is not None else None,
        )

    return blocks_for
