"""The review use cases: what one review run does, over injected ports.

The application layer between the entry points and the engine. ``run_review``
owns the order of a pull-request review run: incremental scoping, the
whole-PR risk assessment, learned feedback, dropping LEFT-side findings on an
incremental run, the watermark and diff fingerprint, labels and the merge-gate
check run, the posting order, and diagram completion. ``run_describe`` and
``run_diagram`` are the same kind of use case for the describe and overview
comments (``run_diagram`` is also the last step of a review that requires one).

Every function here takes its gateway, engine and provider as arguments and
probes optional gateway capabilities with ``isinstance(gateway, SupportsX)``.
It imports nothing from ``lgtmaybe.cli``: the CLI is one composition root that
builds the real adapters and calls in, and a second entry point (the MCP
server, the eval harness, a library caller) calls the same functions rather
than copying the flow.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from lgtmaybe.core.comment import (
    DIAGRAM_MARKER_FAMILY,
    current_finding_keys,
    find_existing_overview,
    marker,
)
from lgtmaybe.core.diff import diff_fingerprint
from lgtmaybe.core.diffparse import split_by_file
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import PRContext, ReviewConfig, ReviewFinding, Severity
from lgtmaybe.core.ports import (
    ProviderClient,
    ReviewEngine,
    ReviewGateway,
    SupportsChecks,
    SupportsCompletionMark,
    SupportsConversation,
    SupportsDescribe,
    SupportsDiagram,
    SupportsDiffFingerprint,
    SupportsFeedback,
    SupportsIncremental,
    SupportsLabels,
    SupportsScanManifests,
    SupportsThreadResolution,
)
from lgtmaybe.engine import INCOMPLETE_MARKER
from lgtmaybe.engine.profiling import profiler

_log = get_logger(__name__)


def marker_key(cfg: ReviewConfig) -> str:
    """Scopes this setup's hidden markers, so each provider/model owns its comments."""
    return f"{cfg.provider}/{cfg.model}"


def fetch_context(gateway: ReviewGateway, cfg: ReviewConfig) -> PRContext:
    """Fetch the PR context, arming the manifest prefetch when the gateway can.

    Dependency manifests are fetched only when a scanner will read them — an
    optional capability, probed like every other, so a gateway implementing
    just the frozen port still works.
    """
    if isinstance(gateway, SupportsScanManifests):
        gateway.set_scan_manifests(cfg.static_analysis.enabled)
    return gateway.get_pr_context()


def existing_overview_note(github: ReviewGateway, ctx: PRContext, cfg: ReviewConfig) -> str | None:
    """The summary note that replaces the automatic overview, or None to post it.

    A PR whose description or conversation already diagrams the change gets no
    second overview from us — the review says where the existing one is and how
    to ask for ours. Best-effort in one direction only: a failed comment listing
    still checks the description, and anything short of a found diagram posts.
    """
    comments: list[tuple[str, str]] = []
    if isinstance(github, SupportsConversation):
        try:
            comments = github.list_conversation_comments()
        except Exception as exc:  # noqa: BLE001 - the scan may only remove a duplicate
            _log.warning("reading PR comments for an existing overview failed: %s", exc)
    where = find_existing_overview(
        ctx.description, comments, own_marker=marker(DIAGRAM_MARKER_FAMILY, marker_key(cfg))
    )
    if where is None:
        return None
    _log.info("change overview already on the PR — auto overview skipped", extra={"where": where})
    return (
        f"_Change overview not posted: {where} already diagrams this change. "
        "Comment `/diagram` to post lgtmaybe's._"
    )


def run_review(
    *,
    github: ReviewGateway,
    engine: ReviewEngine,
    cfg: ReviewConfig,
    dry_run: bool,
    ctx: PRContext | None = None,
    provider: ProviderClient | None = None,
    diagram_required: bool = False,
) -> tuple[list[ReviewFinding], str]:
    """Run a full or hybrid review and optionally persist its completion state.

    Fetches PR context (unless a prefetched ``ctx`` is passed in), runs the
    engine, and optionally posts the review. Returns (findings, summary) in
    all cases so callers can inspect output. ``provider`` performs explicit
    validation of earlier findings and builds a required automatic diagram.
    ``diagram_required`` makes that current-head diagram part of completion,
    unless the PR already diagrams the change (``existing_overview_note``): then
    the review alone completes the head and its summary names the skip.

    With ``cfg.incremental`` on and a gateway that supports it, only the diff
    since the last completed head is reviewed while earlier findings are
    validated. A completed same-head run is a no-op; every degraded case
    (first review, force-push, compare failure, incomplete state, or a gateway
    without the adapter methods) falls back to a full review.
    """
    if ctx is None:
        ctx = fetch_context(github, cfg)
    overview_note = None
    if diagram_required and not dry_run:
        # Checked here, on whichever context this run holds, so a failed
        # prefetch upstream cannot bypass it.
        overview_note = existing_overview_note(github, ctx, cfg)
        diagram_required = overview_note is None
    review_ctx, incremental_since, nothing_to_review = _incremental_context(
        github, ctx, cfg, diagram_required=diagram_required
    )
    if nothing_to_review is not None:
        return [], nothing_to_review
    if cfg.risk.enabled and ctx.risk is None:
        # Assessed on `ctx`, the WHOLE pull request, not on `review_ctx`, which
        # incremental scoping may have narrowed: every surface (summary, labels,
        # overview) then reads this one verdict, and a one-line follow-up push
        # reports the level of the PR it belongs to. The workspace is the
        # engine's default: the cwd.
        from lgtmaybe.engine.risk import assess_risk

        with profiler.stage("risk"):
            risk = assess_risk(ctx, cfg, Path.cwd())
        ctx = ctx.model_copy(update={"risk": risk})
        review_ctx = review_ctx.model_copy(update={"risk": risk})
    review_ctx = _apply_learned_feedback(github, review_ctx, cfg)
    findings, summary = engine.review(review_ctx, cfg)

    if incremental_since is not None:
        # LEFT-side line numbers in an incremental diff are relative to the
        # last-reviewed head, not the PR base — posting them would mis-anchor,
        # so a (rare) deleted-line finding is dropped rather than mis-posted.
        dropped = [f for f in findings if f.side != "RIGHT"]
        if dropped:
            _log.info(
                "dropped LEFT-side findings in incremental mode",
                extra={"count": len(dropped)},
            )
        findings = [f for f in findings if f.side == "RIGHT"]
        validation_summary = _validate_prior_findings(github, provider, cfg, review_ctx, findings)
        summary += (
            f"\n\n_Incremental review of the changes since {incremental_since[:7]}. "
            "Earlier findings stay open until fixed._"
        )
        if validation_summary:
            summary += f"\n\n{validation_summary}"
    if overview_note:
        summary += f"\n\n{overview_note}"

    if not dry_run:
        # Prepare the watermark for the review body. This is only in-memory
        # adapter state until post_review succeeds, so an interruption here
        # cannot remotely complete the head.
        complete = INCOMPLETE_MARKER not in summary
        if isinstance(github, SupportsCompletionMark):
            github.mark_reviewed(ctx.head_sha if complete else None)
        if isinstance(github, SupportsDiffFingerprint):
            # The WHOLE pull request's diff, never the increment: the next run
            # compares it against its own whole diff (see _incremental_context).
            github.mark_reviewed_diff(diff_fingerprint(ctx.diff) if complete else None)
        if incremental_since is not None and isinstance(github, SupportsIncremental):
            # Resolve-on-fix may only touch threads on files this run
            # actually re-reviewed — absence elsewhere proves nothing.
            github.set_incremental_scope(_diff_paths(review_ctx.diff))
        # Make an incomplete run visible. On a re-run post_review can only PUT
        # the summary onto the review the FIRST run created — a silent edit of an
        # older comment that notifies nobody, while this run's new findings post
        # as individual review comments (which GitHub wraps in bodiless reviews).
        # A partial review would then be indistinguishable from a clean one, so
        # the notice also posts as its own PR comment. Only when a call actually
        # failed, so a healthy review adds no noise; not swallowed, because a
        # disclosure that silently fails to post is the bug this fixes. Posted
        # BEFORE the review for the reason given below: nothing may follow that
        # write, and a disclosure lost to a cancelled run is the whole bug.
        if INCOMPLETE_MARKER in summary:
            _log.warning("review incomplete — posting a visible notice on the PR")
            github.post_issue_comment(summary)
        if cfg.pr_labels and isinstance(github, SupportsLabels):
            # Effort/risk labels from data already computed — best-effort,
            # and only on gateways that support them (fakes don't).
            from lgtmaybe.engine.labels import compute_labels

            github.apply_pr_labels(compute_labels(findings, ctx))
        if cfg.fail_on is not None and isinstance(github, SupportsChecks):
            # Merge-gate: create a Check Run so branch protection can require it.
            # Enforcement rides the Check Run, never PR approval state. Best-effort
            # and only on gateways that support it (fakes/plain gateways don't).
            conclusion, title, check_summary = fail_on_check(findings, cfg.fail_on)
            github.create_check_run(ctx.head_sha, conclusion, title, check_summary)
        # Last write of the run, on purpose: the inline comments this posts fire
        # pull_request_review_comment events, so a consumer whose concurrency
        # group isn't discriminated by event name can have the resulting run
        # cancel this one. Nothing may follow that we would lose.
        #
        # Pass the FULL PR diff (already fetched) so the commentable-line
        # index is built from the diff the comments will anchor to — the
        # incremental diff's context lines aren't necessarily in the PR diff.
        with profiler.stage("post"):
            github.post_review(findings, summary, diff=ctx.diff)
        if diagram_required and complete:
            if provider is None:
                raise ValueError("a provider is required for automatic diagram completion")
            run_diagram(github, provider, cfg, ctx=ctx, completed_sha=ctx.head_sha)

    return findings, summary


def _incremental_context(
    github: ReviewGateway,
    ctx: PRContext,
    cfg: ReviewConfig,
    *,
    diagram_required: bool = False,
) -> tuple[PRContext, str | None, str | None]:
    """Return ``(context, completed_sha, nothing_to_review)`` for this run.

    Incremental only when ``cfg.incremental`` is truthy (None = auto resolves
    upstream, and unresolved auto means full), the gateway is
    ``SupportsIncremental`` (fakes and the frozen port aren't), a completion
    watermark exists, the head moved since, and the compare yields a usable
    increment. Every degraded case returns the full context with no completed
    SHA.

    ``nothing_to_review`` is the reason this run has no work, or None: the head
    is the completed one, or a new head whose whole diff fingerprints the same
    as the completed head's (a rebase that only moved the PR, as GitHub does to
    every stacked PR above one that merges).
    """
    if not cfg.incremental or not isinstance(github, SupportsIncremental):
        return ctx, None, None
    last_sha = github.last_completed_sha(diagram_required=diagram_required)
    if not last_sha:
        return ctx, None, None
    if last_sha == ctx.head_sha:
        return ctx, None, f"Head {ctx.head_sha[:7]} is already complete; nothing changed."
    if _diff_unchanged_since(github, ctx, last_sha):
        _log.info("diff unchanged since the last review", extra={"since": last_sha})
        return (
            ctx,
            None,
            f"Head {ctx.head_sha[:7]} has the same diff as the completed {last_sha[:7]} "
            "(for example a rebase that only moved the pull request onto a new base): "
            "the diff is unchanged, so there is nothing new to review.",
        )
    increment = github.compare_diff(last_sha, ctx.head_sha)
    if not increment or not increment.strip():
        return ctx, None, None
    _log.info(
        "incremental review",
        extra={"since": last_sha, "head": ctx.head_sha},
    )
    return ctx.model_copy(update={"diff": increment}), last_sha, None


def _diff_unchanged_since(github: SupportsIncremental, ctx: PRContext, last_sha: str) -> bool:
    """Whether ``ctx``'s whole diff fingerprints the same as ``last_sha``'s did.

    Only when the stamped fingerprint belongs to ``last_sha`` itself: with a
    diagram required, the last completed head can be older than the last
    reviewed one, whose fingerprint then describes a different head. A gateway
    without ``SupportsDiffFingerprint`` (every forge but GitHub) never skips.
    """
    if not isinstance(github, SupportsDiffFingerprint):
        return False
    if github.last_reviewed_sha() != last_sha:
        return False
    stamped = github.last_reviewed_diff()
    return stamped is not None and stamped == diff_fingerprint(ctx.diff)


def _diff_paths(diff: str) -> set[str]:
    """The file paths named by a unified diff's ``diff --git`` headers."""
    return {path for path, _patch in split_by_file(diff, []) if path != "unknown"}


def _apply_learned_feedback(github: ReviewGateway, ctx: PRContext, cfg: ReviewConfig) -> PRContext:
    """Attach 👎-downvoted finding fingerprints to ``ctx.feedback_downvotes``.

    A human with write access reacting thumbs-down to one of our inline finding
    comments is a signal to stop surfacing that finding on this PR. We re-read
    those reactions from GitHub each run (no new persistence). The engine's
    suppression pass then drops the matching findings — except high/critical
    security findings, which a downvote can never hide (see ``suppress.py``).

    Best-effort: disabled by ``learn_feedback=False``, a no-op on a gateway
    without ``SupportsFeedback`` (fakes, the frozen port), and any error is
    swallowed so a feedback-read hiccup can never fail the review.
    """
    if not cfg.learn_feedback or not isinstance(github, SupportsFeedback):
        return ctx
    try:
        downvoted = github.list_downvoted_fingerprints()
    except Exception as exc:  # noqa: BLE001 — best-effort; never fail the review
        _log.warning("reading downvoted findings failed: %s", exc)
        return ctx
    if not downvoted:
        return ctx
    _log.info("suppressing downvoted findings from feedback", extra={"count": len(downvoted)})
    return ctx.model_copy(update={"feedback_downvotes": frozenset(downvoted)})


def _validate_prior_findings(
    github: ReviewGateway,
    provider: ProviderClient | None,
    cfg: ReviewConfig,
    ctx: PRContext,
    current_findings: list[ReviewFinding],
) -> str:
    """Validate active prior findings and install the explicit resolve allowlist."""
    if not isinstance(github, SupportsThreadResolution):
        return ""
    set_fixed = github.set_validated_fixed_threads
    try:
        active = github.list_active_findings()
    except Exception as exc:  # noqa: BLE001 - failure leaves every thread open
        _log.warning("reading active findings for validation failed: %s", exc)
        set_fixed(set())
        return "_Follow-up validation was unavailable; earlier findings remain open._"
    if not active:
        set_fixed(set())
        return ""

    from lgtmaybe.core.models import FindingValidation, FindingValidationStatus
    from lgtmaybe.engine.validate import validate_findings

    current_keys = current_finding_keys(current_findings)
    reproduced = [
        finding
        for finding in active
        if ({finding.fingerprint, finding.identity} - {None}) & current_keys
    ]
    reproduced_thread_ids = {finding.thread_id for finding in reproduced}
    unmatched = [finding for finding in active if finding.thread_id not in reproduced_thread_ids]
    verdicts = [
        FindingValidation(
            thread_id=finding.thread_id,
            status=FindingValidationStatus.still_open,
            reason="the incremental review reproduced this finding",
        )
        for finding in reproduced
    ]
    if provider is None:
        verdicts.extend(
            FindingValidation(
                thread_id=finding.thread_id,
                status=FindingValidationStatus.uncertain,
                reason="no provider was available for follow-up validation",
            )
            for finding in unmatched
        )
    else:
        verdicts.extend(validate_findings(provider, cfg, unmatched, ctx))

    outdated_thread_ids = {finding.thread_id for finding in active if finding.outdated}
    verdicts = [
        FindingValidation(
            thread_id=verdict.thread_id,
            status=FindingValidationStatus.uncertain,
            reason="the model reported a fix but the forge does not mark the finding outdated",
        )
        if verdict.status is FindingValidationStatus.fixed
        and verdict.thread_id not in outdated_thread_ids
        else verdict
        for verdict in verdicts
    ]

    fixed = {
        verdict.thread_id for verdict in verdicts if verdict.status is FindingValidationStatus.fixed
    }
    set_fixed(fixed)
    counts = Counter(verdict.status for verdict in verdicts)
    return (
        f"_Follow-up validation: {counts[FindingValidationStatus.fixed]} fixed, "
        f"{counts[FindingValidationStatus.still_open]} still open, "
        f"{counts[FindingValidationStatus.uncertain]} uncertain._"
    )


def fail_on_check(findings: list[ReviewFinding], fail_on: Severity) -> tuple[str, str, str]:
    """Build the (conclusion, title, summary) for the merge-gate Check Run.

    ``failure`` when any surviving finding is at or above ``fail_on`` (the
    findings are already the severity-floor-filtered set), else ``success``.
    The summary carries the count at/above the threshold plus a per-severity
    breakdown so the check page explains the verdict.
    """
    failing = [f for f in findings if f.severity >= fail_on]
    conclusion = "failure" if failing else "success"
    counts = Counter(f.severity for f in findings)
    breakdown = ", ".join(f"{counts[s]} {s.value}" for s in Severity if counts[s]) or "none"
    if failing:
        title = f"{len(failing)} finding(s) at or above {fail_on.value}"
    else:
        title = f"No findings at or above {fail_on.value}"
    summary = (
        f"{len(failing)} of {len(findings)} finding(s) are at or above the "
        f"`{fail_on.value}` threshold.\n\nFindings by severity: {breakdown}."
    )
    return conclusion, title, summary


def run_describe(
    github: ReviewGateway,
    provider: ProviderClient,
    cfg: ReviewConfig,
    ctx: PRContext | None = None,
) -> None:
    """Build the structured description and post it idempotently.

    Pure function over injected ports, like ``run_review``. Prefers the
    gateway's upsert method; falls back to a plain issue comment. A prefetched
    ``ctx`` avoids repeating the expensive PR-context fetch.
    """
    from lgtmaybe.engine.describe import build_description

    body = build_description(github.get_pr_context() if ctx is None else ctx, cfg, provider)
    if isinstance(github, SupportsDescribe):
        github.post_describe_comment(body)
        return
    github.post_issue_comment(body)


def run_diagram(
    github: ReviewGateway,
    provider: ProviderClient,
    cfg: ReviewConfig,
    ctx: PRContext | None = None,
    *,
    completed_sha: str | None = None,
) -> None:
    """Build the change overview and post it idempotently.

    The comment carries the description, the High Impact Areas section and the
    diagrams — each gated by its own config flag, all in one upsert under the
    diagram marker family.
    """
    from lgtmaybe.engine.overview import build_overview

    body = build_overview(github.get_pr_context() if ctx is None else ctx, cfg, provider)
    if isinstance(github, SupportsDiagram):
        github.post_diagram_comment(body, completed_sha=completed_sha)
        return
    if completed_sha is not None:
        raise RuntimeError("the GitHub gateway cannot persist a diagram completion marker")
    # The required port's plain comment; a failure to post propagates, because
    # a diagram the user asked for must not vanish silently.
    github.post_issue_comment(body)
