"""LLMReviewEngine: the full review pipeline.

Pipeline: redact → compress/batch → (per batch) fan out one call per review
         category (concurrent for cloud, serial for ollama) → parse → merge/dedupe
         → self-reflect/filter → filter by min_severity → return findings + summary.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial

from lgtmaybe.core.diffparse import changed_line_index, split_by_file
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ReviewCategory,
    ReviewConfig,
    ReviewFinding,
    ReviewResult,
)
from lgtmaybe.core.ports import Message, ProviderClient, ReviewEngine
from lgtmaybe.github import is_reviewable

from .astgrep import SymbolResolver
from .compress import batch_files, context_lines_for_budget, count_tokens, expand_hunks
from .injection import wrap_diff, wrap_intent
from .parse import ParseError, parse_findings
from .prompt import build_lens_prompt, build_system_prompt
from .redact import redact
from .reflect import reflect_findings
from .retrieve import FileFetcher
from .suppress import apply_suppressions

_log = get_logger(__name__)

# A single ollama instance serves a model serially, so concurrent calls only
# queue up and time out; every other provider parallelises across categories.
# The ceiling is kept modest so the per-batch fan-out doesn't burst the whole
# lens set at a provider at once and trip a capacity rate-limit (429) on
# lower-tier accounts — the lenses just run in a couple of waves instead, and
# per-call latency dominates so the wall-clock cost is small.
_MAX_WORKERS = 4


@dataclass(frozen=True)
class _Lens:
    """One review lens in the fan-out: a built-in category or a user-defined lens.

    Holds the prebuilt system prompt so the fan-out is uniform — the engine no
    longer cares whether a lens came from ``ReviewCategory`` or ``extra_lenses``.
    ``carries_intent`` is true only for the built-in intent lens, the one call
    that receives the stated-intent block.
    """

    id: str
    system_prompt: str
    carries_intent: bool = False


def _build_lenses(cfg: ReviewConfig) -> list[_Lens]:
    """All lenses to run: built-in categories first, then user-defined lenses."""
    lenses = [
        _Lens(
            id=category.value,
            system_prompt=build_system_prompt(category),
            carries_intent=category is ReviewCategory.intent,
        )
        for category in cfg.categories
    ]
    lenses += [
        _Lens(id=lens.id, system_prompt=build_lens_prompt(lens)) for lens in cfg.extra_lenses
    ]
    return lenses


class ReviewIncompleteError(Exception):
    """Every review call failed (timeout or unparseable output) — no usable result.

    Raised instead of silently reporting a clean review, so the CLI surfaces a
    failure (non-zero exit / failure comment) rather than a false 👍 LGTM.
    """


def _worker_count(cfg: ReviewConfig, lens_count: int) -> int:
    """How many lens calls to run at once: 1 for ollama (serial backend)."""
    if cfg.provider is Provider.ollama:
        return 1
    return min(lens_count, _MAX_WORKERS) or 1


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
        # 1. Redact secrets from the diff before it leaves this process.
        clean_diff = redact(ctx.diff)

        # 1b. Stated intent (PR title/description/commit names) for the intent
        #     lens: redacted like the diff, wrapped as untrusted data, and only
        #     ever sent on the intent call. No stated intent → skip that lens
        #     rather than burn a model call judging the diff against nothing.
        intent_text = _intent_text(ctx)
        intent_block = wrap_intent(redact(intent_text)) if intent_text else None
        lenses = _build_lenses(cfg)
        if intent_block is None and any(lens.carries_intent for lens in lenses):
            lenses = [lens for lens in lenses if not lens.carries_intent]
            _log.info("intent lens skipped — no stated intent (title/description/commits)")

        # 2. Split into per-file patches and drop generated/binary/vendored noise.
        file_patches = split_by_file(clean_diff, ctx.changed_files)
        file_patches = [(path, patch) for path, patch in file_patches if is_reviewable(path)]

        # 3. File cap: review only the first N reviewable files, note the rest.
        total_files = len(file_patches)
        capped_files = total_files > cfg.max_files
        if capped_files:
            file_patches = file_patches[: cfg.max_files]

        # 4. Pad each hunk with surrounding lines so the model sees the function
        #    and definitions around a change. The amount is budget-scaled and
        #    capped by cfg.context_lines; content is the head file text the
        #    gateway fetched (redacted), and is for understanding only —
        #    inline-comment positions are always built from the real diff.
        used_tokens = count_tokens(clean_diff)
        remaining = max(0, cfg.max_input_tokens - used_tokens)
        ctx_lines = min(cfg.context_lines, context_lines_for_budget(remaining))
        if ctx_lines > 0 and ctx.file_contents:
            file_patches = [
                (path, expand_hunks(patch, redact(ctx.file_contents.get(path, "")), ctx_lines))
                for path, patch in file_patches
            ]

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

        # 5. For each batch, fan out one call per review category. Each category
        #    gets a focused prompt; their findings are merged. Concurrency is
        #    provider-aware — serial for ollama so calls don't queue and time out.
        workers = _worker_count(cfg, len(lenses))
        # Constrain output to the findings schema (provider-native JSON mode) per
        # review call — NOT globally, so the reflection call keeps its own format.
        response_format = ReviewResult if cfg.structured_output else None
        for batch_num, batch in enumerate(batches, start=1):
            batch_diff = "\n".join(patch for _, patch in batch)
            wrapped = wrap_diff(batch_diff)
            review_one = partial(
                self._review_lens, wrapped, intent_block, cfg.model, response_format
            )
            if len(batches) > 1:
                _log.info(
                    "reviewing batch",
                    extra={"batch": batch_num, "batches": len(batches)},
                )
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for findings, error in pool.map(review_one, lenses):
                    total_calls += 1
                    if error is not None:
                        failed_calls += 1
                        errors.append(error)
                    all_findings.extend(findings)

        # 5b. Fail loud: if EVERY call errored or returned unparseable output, we
        #     have no signal — never pass that off as a clean review.
        if total_calls > 0 and failed_calls == total_calls:
            detail = errors[-1] if errors else "no usable output"
            raise ReviewIncompleteError(
                f"review incomplete — every review call failed ({detail}). "
                "Check the provider credentials/quota, model, and timeout "
                "(ollama: a larger model needs a longer --timeout), then retry."
            )

        reviewed_diff = "\n".join(patch for _, patch in file_patches)

        # 6. Re-anchor: the model hand-counts line numbers from the hunk header and
        #    routinely drifts a few lines. Snap each finding's line to the real
        #    changed line whose content matches its verbatim `anchor`, so comments
        #    land on the code they describe. Done before dedupe so findings the
        #    model placed on slightly different wrong lines collapse correctly.
        all_findings = _snap_findings(all_findings, reviewed_diff)

        # 7. Merge: a finding can surface under more than one lens (a shell
        #    injection is both a security and a correctness issue), so collapse
        #    duplicates before reflecting.
        all_findings = _dedupe(all_findings)

        # 7b. Suppression: drop findings a team has marked known-fine — by
        #     fingerprint (cfg.ignore_fingerprints) or an inline `# lgtmaybe:
        #     ignore` pragma on/above the flagged line. Done before reflection so a
        #     suppressed finding costs no reflection tokens and never posts.
        before_suppress = len(all_findings)
        all_findings = apply_suppressions(all_findings, cfg, ctx.file_contents)
        suppressed = before_suppress - len(all_findings)
        if suppressed:
            _log.info("suppressed findings", extra={"count": suppressed})

        # 8. Self-reflection: filter out low-confidence findings. Reflect against
        #    only the reviewed diff — redacted, and free of skipped/over-cap files.
        #    Skippable (--no-reflect) for weaker models that drop valid findings here.
        if cfg.reflect and all_findings:
            _log.info("reflecting on findings", extra={"findings": len(all_findings)})
            # model_copy keeps file_contents — reflection now grounds itself on the
            # (redacted) head text of flagged files to verify whole-file claims.
            clean_ctx = ctx.model_copy(update={"diff": reviewed_diff})
            all_findings = reflect_findings(
                all_findings,
                clean_ctx,
                cfg,
                self._provider,
                fetch_file=self._fetch_file,
                resolve_symbol=self._resolve_symbol,
            )

        # 9. Filter: drop findings below the severity floor, and apply the
        #    stricter unanchored floor — a finding the engine could not anchor is a
        #    low-confidence guess, so surface it only when it's high/critical.
        filtered = [f for f in all_findings if _passes_severity_floor(f, cfg)]

        plural = "s" if len(filtered) != 1 else ""
        summary_line = (
            f"{len(filtered)} finding{plural} · provider {cfg.provider} · model {cfg.model}"
        )

        notices = []
        if capped_files:
            notices.append(
                f"⚠️ Reviewed the top {cfg.max_files} of {total_files} changed files "
                f"(file cap {cfg.max_files}). Raise max_files to review them all."
            )
        # Some — but not all — lenses failed: the result may be incomplete, so say
        # so and don't claim a clean bill of health.
        if failed_calls:
            detail = errors[-1] if errors else "timeout or unparseable output"
            notices.append(
                f"⚠️ {failed_calls} of {total_calls} review calls failed "
                f"({detail}); results may be incomplete."
            )

        if notices:
            return filtered, "\n\n".join([*notices, summary_line])
        # A genuinely clean review (nothing flagged, every call succeeded) gets an
        # explicit thumbs-up rather than a bare "0 findings".
        if not filtered:
            return filtered, f"👍 LGTM!\n\n{summary_line}"
        return filtered, summary_line

    def _review_lens(
        self,
        wrapped: str,
        intent_block: str | None,
        model: str,
        response_format: type[ReviewResult] | None,
        lens: _Lens,
    ) -> tuple[list[ReviewFinding], str | None]:
        """Run one focused review call for a single lens (built-in or user-defined).

        Returns ``(findings, error)``. ``error`` is None on success, else a concise
        reason — the provider exception (e.g. a 429 quota error) or unparseable
        output — that the engine surfaces so a failure names its real cause instead
        of a generic "timeout". A failing lens never aborts the others.
        """
        user_content = wrapped
        if lens.carries_intent and intent_block is not None:
            # Only the intent lens pays the intent-block tokens (and its
            # injection surface); the other lenses never see PR-authored prose.
            user_content = f"{intent_block}\n\n{wrapped}"
        messages: list[Message] = [
            {"role": "system", "content": lens.system_prompt},
            {"role": "user", "content": user_content},
        ]
        opts = {"response_format": response_format} if response_format is not None else {}
        # Heartbeat: log the call going out and coming back so the Action shows
        # steady per-lens progress while the model runs, not a silent gap.
        _log.info("reviewing lens", extra={"lens": lens.id})
        try:
            result = self._provider.complete(messages, model=model, **opts)
        except Exception as exc:
            reason = _error_reason(exc)
            _log.warning(
                "review call failed",
                extra={"lens": lens.id, "reason": reason},
                exc_info=True,
            )
            return [], reason
        try:
            findings = parse_findings(result.text)
        except ParseError:
            _log.warning("unparseable model output", extra={"lens": lens.id})
            return [], "unparseable model output"
        _log.info("lens reviewed", extra={"lens": lens.id, "findings": len(findings)})
        return findings, None


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
        substring = [
            line
            for line, text, stripped, _norm in candidates
            if target in text or stripped in target
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
