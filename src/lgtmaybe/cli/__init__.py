"""CLI for lgtmaybe.

This package is split into three layers:

- ``runtime`` / ``render`` — small, pure helpers (call-time options, output
  formatting).
- this module — the *logic*: parsing, adapter/provider wiring, and the
  ``execute_*`` entry points that the commands call. Kept together so the
  pipeline stages resolve (and can be patched in tests) as one namespace.
- ``commands`` — the Click command + option declarations, imported at the
  bottom to register onto the groups defined here.
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from typing import Any

import click

from lgtmaybe.cli.render import render_findings
from lgtmaybe.cli.runtime import RuntimeOptions
from lgtmaybe.core.diffparse import FILE_HEADER_RE, hunk_for_line
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ReviewConfig,
    ReviewFinding,
    ReviewPreset,
    Severity,
)
from lgtmaybe.core.ports import GitHubGateway, ProviderClient, ReviewEngine
from lgtmaybe.engine import (
    INCOMPLETE_MARKER,
    FileFetcher,
    LLMReviewEngine,
    SymbolResolver,
    build_symbol_resolver,
)
from lgtmaybe.engine.profiling import profiler
from lgtmaybe.github import RestGitHubGateway
from lgtmaybe.local import local_file_reader, local_pr_context
from lgtmaybe.providers.credentials import resolve_credentials
from lgtmaybe.providers.factory import (
    build_provider,
    cheaper_reflect_sibling,
    default_timeout_for,
)

_log = get_logger(__name__)

_PR_URL_RE = re.compile(r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)")


def package_version() -> str:
    """The installed lgtmaybe version, or ``"unknown"`` when it can't be read.

    Reading it is best-effort by design: a source checkout that was never
    installed has no distribution metadata, and a missing version must never be
    the reason a review fails.
    """
    try:
        return metadata.version("lgtmaybe")
    except Exception:
        return "unknown"


def _should_auto_post(enabled: bool, event_action: str) -> bool:
    """One gate for both auto extras: opted in, and the PR was just opened or
    reopened — a synchronize push updates the review, not the extras."""
    return enabled and event_action in ("opened", "reopened")


def should_auto_describe(cfg: ReviewConfig, *, event_action: str) -> bool:
    """Whether the action run should post the structured description first."""
    return _should_auto_post(cfg.auto_describe, event_action)


def should_auto_diagram(cfg: ReviewConfig, *, event_action: str) -> bool:
    """Whether the action run should post the change diagram first."""
    return _should_auto_post(cfg.auto_diagram, event_action)


def _run_upsert(
    github: GitHubGateway,
    provider: ProviderClient,
    cfg: ReviewConfig,
    *,
    build: Callable[[PRContext, ReviewConfig, ProviderClient], str],
    post_method: str,
    ctx: PRContext | None,
) -> None:
    """Build a describe/diagram body and post it idempotently.

    Pure function over injected ports, like ``run_review``. Prefers the
    gateway's upsert method; falls back to a plain issue comment. A prefetched
    ``ctx`` avoids repeating the expensive PR-context fetch.
    """
    body = build(github.get_pr_context() if ctx is None else ctx, cfg, provider)
    post = getattr(github, post_method, None)
    if post is not None:
        post(body)
        return
    post_comment = getattr(github, "post_issue_comment", None)
    if post_comment is not None:
        post_comment(body)


def run_describe(
    github: GitHubGateway,
    provider: ProviderClient,
    cfg: ReviewConfig,
    ctx: PRContext | None = None,
) -> None:
    """Build the structured description and post it idempotently."""
    from lgtmaybe.engine.describe import build_description

    _run_upsert(
        github, provider, cfg, build=build_description, post_method="post_describe_comment", ctx=ctx
    )


def run_diagram(
    github: GitHubGateway,
    provider: ProviderClient,
    cfg: ReviewConfig,
    ctx: PRContext | None = None,
) -> None:
    """Build the change diagram and post it idempotently."""
    from lgtmaybe.engine.diagram import build_diagram

    _run_upsert(
        github, provider, cfg, build=build_diagram, post_method="post_diagram_comment", ctx=ctx
    )


def resolve_auto_incremental(cfg: ReviewConfig, *, event_action: str) -> ReviewConfig:
    """Resolve ``incremental=None`` (auto) against the triggering event's action.

    Auto means: incremental on a ``synchronize`` push (the PR already had a
    review to be incremental against, and only new commits landed), full
    everywhere else (opened/reopened/manual). An explicit True/False from
    config or the Action input always wins.
    """
    if cfg.incremental is not None:
        return cfg
    return cfg.model_copy(update={"incremental": event_action == "synchronize"})


def parse_pr_url(pr_url: str) -> tuple[str, int]:
    """Parse a GitHub PR URL into ("owner/repo", pr_number).

    Raises ValueError with a clear message for anything that is not a PR URL.
    """
    match = _PR_URL_RE.search(pr_url)
    if match is None:
        raise ValueError(
            f"Could not parse a GitHub PR URL from {pr_url!r}. "
            "Expected something like https://github.com/org/repo/pull/42"
        )
    return f"{match['owner']}/{match['repo']}", int(match["number"])


def build_provider_engine(
    cfg: ReviewConfig,
    runtime: RuntimeOptions,
    *,
    fetch_file: FileFetcher | None = None,
    resolve_symbol: SymbolResolver | None = None,
) -> tuple[LLMReviewEngine, ProviderClient]:
    """Resolve credentials and build the provider + engine from config + runtime.

    Shared by every path that needs to talk to the model — the GitHub gateway
    wiring and the local review alike — so credential resolution and provider
    options stay in exactly one place. Raises ValueError with an actionable
    message when a required credential is missing.
    """
    # Fast preset: default the reflection audit to a cheaper sibling of the
    # review model when one is confidently resolvable (anthropic/openai only —
    # see cheaper_reflect_sibling). An explicit reflect_model always wins, and
    # the full preset keeps auditing with the review model as before.
    if cfg.preset is ReviewPreset.fast and cfg.reflect_model is None:
        sibling = cheaper_reflect_sibling(cfg.provider, cfg.model)
        if sibling is not None:
            _log.info("fast preset: reflecting with a cheaper sibling", extra={"model": sibling})
            cfg = cfg.model_copy(update={"reflect_model": sibling})

    auth = resolve_credentials(
        cfg.provider,
        api_key=runtime.api_key,
        api_base=runtime.api_base or cfg.api_base,
    )
    # num_ctx is ollama's context window — hosted providers manage theirs
    # server-side and litellm rejects the option, so only forward it for ollama.
    extra: dict[str, Any] = {}
    if cfg.provider is Provider.ollama and cfg.num_ctx is not None:
        extra["num_ctx"] = cfg.num_ctx
    # An output cap is only sent when asked for: omitting it lets the model's own
    # ceiling apply, so nothing truncates a long findings payload by default.
    # Every provider honours it (litellm normalises the param), so unlike num_ctx
    # it is not gated on the provider.
    if cfg.max_tokens is not None:
        extra["max_tokens"] = cfg.max_tokens
    # Announce the per-call budget AND where it came from, before any call runs.
    # A timeout failure reports the budget it blew ("provider request exceeded
    # 60s") but never its origin, so an explicit `timeout: 60` in a repo's
    # .lgtmaybe.yml and a 60s built-in default leave identical evidence — which
    # is exactly the ambiguity that makes "why is this timing out?" unanswerable
    # from a log. `source` is what separates them.
    #
    # The version is here for the same reason. The Action's `image` input pins a
    # FLOATING tag, so the action.yml a user reads and the code that runs are
    # versioned independently: a budget that looks impossible against the
    # documented default is usually an older image, and the only way to tell from
    # a log is for the run to name its own build.
    effective_timeout = (
        cfg.timeout if cfg.timeout is not None else default_timeout_for(cfg.provider)
    )
    _log.info(
        "per-call timeout resolved",
        extra={
            "lgtmaybe_version": package_version(),
            "provider": cfg.provider.value,
            "timeout_s": effective_timeout,
            "timeout_source": "configured" if cfg.timeout is not None else "provider default",
        },
    )
    provider = build_provider(
        cfg.provider,
        cfg.model,
        api_key=auth.api_key,
        api_base=auth.api_base,
        azure_ad_token=auth.azure_ad_token,
        fallback_model=runtime.fallback_model,
        timeout=cfg.timeout,
        temperature=cfg.temperature,
        prompt_cache=cfg.prompt_cache,
        **extra,
    )
    engine = LLMReviewEngine(provider, fetch_file=fetch_file, resolve_symbol=resolve_symbol)
    return engine, provider


def build_review_context(
    cfg: ReviewConfig, runtime: RuntimeOptions
) -> tuple[RestGitHubGateway, LLMReviewEngine, ProviderClient]:
    """Construct the gateway, engine, and provider from config + runtime.

    Builds the model side via ``build_provider_engine`` and points a REST gateway
    at the parsed PR. Raises ValueError with an actionable message when the
    GitHub token is missing. The provider is returned too so slash commands
    (/ask, /describe) can use it directly.
    """
    if runtime.pr_url is None:
        raise ValueError("a PR URL is required to build the GitHub review context")
    repo, pr_number = parse_pr_url(runtime.pr_url)

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError(
            "GITHUB_TOKEN is required to fetch the PR and post the review. "
            "Set it in the environment (the GitHub Action provides it automatically)."
        )

    github = RestGitHubGateway(
        repo=repo,
        pr_number=pr_number,
        token=token,
        marker_key=f"{cfg.provider}/{cfg.model}",
        resolve_fixed=cfg.resolve_fixed,
    )
    # Wire the gateway's read-only file fetcher so the reflection pass can resolve a
    # deferred verdict (fetch a referenced file the auditor needs) instead of
    # dropping the finding. Read-only API fetch — never a checkout (fork-safe).
    # Cross-file symbol resolution: when the auditor defers on a symbol (not a path),
    # ast-grep finds its defining file in a lazily-cloned checkout of the trusted base
    # branch. Read-only (clone + parse, never execute the PR head), and a no-op when
    # ast-grep is absent. The clone happens only on the first symbol deferral.
    resolve_symbol = (
        build_symbol_resolver(github.base_checkout_root) if cfg.symbol_resolution else None
    )
    engine, provider = build_provider_engine(
        cfg, runtime, fetch_file=github.get_file_contents, resolve_symbol=resolve_symbol
    )
    return github, engine, provider


def build_adapters(
    cfg: ReviewConfig, runtime: RuntimeOptions
) -> tuple[GitHubGateway, ReviewEngine]:
    """Construct the real GitHub gateway and review engine from config + runtime."""
    github, engine, _provider = build_review_context(cfg, runtime)
    return github, engine


def _apply_learned_feedback(github: GitHubGateway, ctx: PRContext, cfg: ReviewConfig) -> PRContext:
    """Attach 👎-downvoted finding fingerprints to ``ctx.feedback_downvotes``.

    A human with write access reacting thumbs-down to one of our inline finding
    comments is a signal to stop surfacing that finding on this PR. We re-read
    those reactions from GitHub each run (no new persistence). The engine's
    suppression pass then drops the matching findings — except high/critical
    security findings, which a downvote can never hide (see ``suppress.py``).

    Best-effort: disabled by ``learn_feedback=False``, a no-op on a gateway
    without the adapter method (fakes, the frozen port), and any error is
    swallowed so a feedback-read hiccup can never fail the review.
    """
    if not cfg.learn_feedback:
        return ctx
    list_downvoted = getattr(github, "list_downvoted_fingerprints", None)
    if list_downvoted is None:
        return ctx
    try:
        downvoted = list_downvoted()
    except Exception as exc:  # noqa: BLE001 — best-effort; never fail the review
        _log.warning("reading downvoted findings failed: %s", exc)
        return ctx
    if not downvoted:
        return ctx
    _log.info("suppressing downvoted findings from feedback", extra={"count": len(downvoted)})
    return ctx.model_copy(update={"feedback_downvotes": frozenset(downvoted)})


def run_review(
    *,
    github: GitHubGateway,
    engine: ReviewEngine,
    cfg: ReviewConfig,
    dry_run: bool,
    ctx: PRContext | None = None,
) -> tuple[list[ReviewFinding], str]:
    """Core review pipeline — pure function over injected ports.

    Fetches PR context (unless a prefetched ``ctx`` is passed in), runs the
    engine, and optionally posts the review. Returns (findings, summary) in
    all cases so callers can inspect output.

    With ``cfg.incremental`` on and a gateway that supports it, only the diff
    of the commits pushed since the last completed review is reviewed; every
    degraded case (first review, force-push, compare failure, a gateway
    without the adapter methods) falls back to the full review.
    """
    if ctx is None:
        ctx = github.get_pr_context()
    review_ctx, incremental_since = _incremental_context(github, ctx, cfg)
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
        summary += (
            f"\n\n_Incremental review of the changes since {incremental_since[:7]} — "
            "earlier findings stay open until fixed._"
        )

    if not dry_run:
        # Record the watermark: this run completed a review of the current
        # head, so the NEXT run may review only what lands after it. Never set
        # on the failure path (a failure notice posts via post_review without
        # this) — an unreviewed commit must not be skipped.
        mark_reviewed = getattr(github, "mark_reviewed", None)
        if mark_reviewed is not None:
            mark_reviewed(ctx.head_sha)
        if incremental_since is not None:
            set_scope = getattr(github, "set_incremental_scope", None)
            if set_scope is not None:
                # Resolve-on-fix may only touch threads on files this run
                # actually re-reviewed — absence elsewhere proves nothing.
                set_scope(_diff_paths(review_ctx.diff))
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
        if cfg.pr_labels:
            # Effort/risk labels from data already computed — best-effort,
            # and only on gateways that support them (fakes don't).
            apply_labels = getattr(github, "apply_pr_labels", None)
            if apply_labels is not None:
                from lgtmaybe.engine.labels import compute_labels

                apply_labels(compute_labels(findings, ctx))
        if cfg.fail_on is not None:
            # Merge-gate: create a Check Run so branch protection can require it.
            # Enforcement rides the Check Run, never PR approval state. Best-effort
            # and only on gateways that support it (fakes/plain gateways don't).
            create_check_run = getattr(github, "create_check_run", None)
            if create_check_run is not None:
                conclusion, title, check_summary = _fail_on_check(findings, cfg.fail_on)
                create_check_run(ctx.head_sha, conclusion, title, check_summary)
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

    return findings, summary


def _fail_on_check(findings: list[ReviewFinding], fail_on: Severity) -> tuple[str, str, str]:
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


def _incremental_context(
    github: GitHubGateway, ctx: PRContext, cfg: ReviewConfig
) -> tuple[PRContext, str | None]:
    """The context to review: ``(incremental ctx, last-reviewed sha)`` or ``(ctx, None)``.

    Incremental only when ``cfg.incremental`` is truthy (None = auto resolves
    upstream, and unresolved auto means full), the gateway has the adapter
    methods (``last_reviewed_sha`` / ``compare_diff`` — fakes and the frozen
    port don't), a watermark exists, head moved since, and the compare yields
    a usable increment. Everything else returns the full context untouched.
    """
    if not cfg.incremental:
        return ctx, None
    last_reviewed_sha = getattr(github, "last_reviewed_sha", None)
    compare_diff = getattr(github, "compare_diff", None)
    if last_reviewed_sha is None or compare_diff is None:
        return ctx, None
    last_sha = last_reviewed_sha()
    if not last_sha or last_sha == ctx.head_sha:
        return ctx, None
    increment = compare_diff(last_sha, ctx.head_sha)
    if not increment or not increment.strip():
        return ctx, None
    _log.info(
        "incremental review",
        extra={"since": last_sha, "head": ctx.head_sha},
    )
    return ctx.model_copy(update={"diff": increment}), last_sha


def _diff_paths(diff: str) -> set[str]:
    """The file paths named by a unified diff's ``diff --git`` headers."""
    return set(FILE_HEADER_RE.findall(diff))


def execute_local_review(
    cfg: ReviewConfig,
    runtime: RuntimeOptions,
    *,
    base: str | None,
    working: bool,
    uncommitted: bool = False,
    fmt: str,
) -> None:
    """Review the local git diff and print findings — no GitHub involvement.

    Builds the provider straight from config/runtime (no token, no gateway),
    runs the engine over the local diff, and prints the result. Any failure
    surfaces as a clean CLI error — there is no PR to post a notice to.
    """
    profiler.reset()
    try:
        # Wire a read-only working-tree reader so reflection can resolve a deferred
        # verdict against the user's own checkout (safe — their branch, not PR code).
        fetch_file = local_file_reader()
        # And let ast-grep resolve a deferred symbol to its defining file by searching
        # that same worktree — the corpus is already on disk, so no clone is needed.
        resolve_symbol = (
            build_symbol_resolver(lambda: Path.cwd()) if cfg.symbol_resolution else None
        )
        engine, _provider = build_provider_engine(
            cfg, runtime, fetch_file=fetch_file, resolve_symbol=resolve_symbol
        )
        ctx = local_pr_context(base=base, working=working, uncommitted=uncommitted)
        findings, summary = engine.review(ctx, cfg)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(render_findings(findings, summary, fmt=fmt))
    if runtime.profile:
        click.echo(profiler.render())


def _execute_auxiliary(
    cfg: ReviewConfig,
    runtime: RuntimeOptions,
    run: Callable[[GitHubGateway, ProviderClient, ReviewConfig], None],
    name: str,
) -> None:
    """Build a fresh context and run one auxiliary post. Best-effort.

    The extras are auxiliary: any failure is logged and swallowed so they can
    never block a review.
    """
    try:
        github, _engine, provider = build_review_context(cfg, runtime)
        run(github, provider, cfg)
    except Exception:
        _log.warning("auto-%s failed — continuing without it", name, exc_info=True)


def execute_describe(cfg: ReviewConfig, runtime: RuntimeOptions) -> None:
    """Post the structured PR description (standalone path). Best-effort."""
    _execute_auxiliary(cfg, runtime, run_describe, "describe")


def execute_diagram(cfg: ReviewConfig, runtime: RuntimeOptions) -> None:
    """Post the change diagram (standalone path). Best-effort."""
    _execute_auxiliary(cfg, runtime, run_diagram, "diagram")


def execute_local_diagram(
    cfg: ReviewConfig,
    runtime: RuntimeOptions,
    *,
    base: str | None,
    working: bool,
    uncommitted: bool = False,
) -> None:
    """Print a compact Mermaid diagram of local changes — no GitHub involvement.

    Builds the provider straight from config/runtime (no token, no gateway) and
    echoes the same Markdown body the ``/diagram`` comment would carry: the
    Mermaid source (paste it into GitHub to render) plus the ASCII rendering,
    which is what actually shows in a terminal.
    """
    from lgtmaybe.engine.diagram import build_diagram

    try:
        _engine, provider = build_provider_engine(cfg, runtime)
        ctx = local_pr_context(base=base, working=working, uncommitted=uncommitted)
        body = build_diagram(ctx, cfg, provider)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(body)


def _post_extras(
    github: GitHubGateway,
    provider: ProviderClient,
    cfg: ReviewConfig,
    ctx: PRContext,
    *,
    describe: bool,
    diagram: bool,
) -> None:
    """Post the auto extras (description, diagram) over a prefetched context.

    Each is independently best-effort: a failure is logged and swallowed so an
    extra can never turn a completed review into a failed run.
    """
    for enabled, run, name in (
        (describe, run_describe, "describe"),
        (diagram, run_diagram, "diagram"),
    ):
        if enabled:
            try:
                run(github, provider, cfg, ctx=ctx)
            except Exception:
                _log.warning("auto-%s failed — continuing without it", name, exc_info=True)


def execute_review(
    cfg: ReviewConfig,
    runtime: RuntimeOptions,
    *,
    describe: bool = False,
    diagram: bool = False,
) -> None:
    """Build adapters, run the review, surface failures back to the PR.

    Shared by the ``review`` command and the ``action`` entrypoint. With
    ``describe``/``diagram`` on (the action's auto extras), the adapters are
    built once and the expensive O(files) PR-context fetch happens once —
    extras and review all reuse them.
    """
    profiler.reset()
    # Adapter construction can fail before we have any way to post (bad URL,
    # missing token/credentials). Surface those as a clean CLI error.
    try:
        github, engine, provider = build_review_context(cfg, runtime)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    ctx: PRContext | None = None
    if describe or diagram:
        # The extras are best-effort and must never block the review. A failed
        # prefetch just means run_review fetches (and surfaces) it itself. The
        # fetch happens here, before the review, so the review reuses it — only
        # the posting is deferred (see _post_extras).
        try:
            ctx = github.get_pr_context()
        except Exception:
            _log.warning("PR context prefetch failed — extras skipped", exc_info=True)

    # From here we have a gateway, so any failure is surfaced back to the PR as
    # a short comment rather than failing silently — then we exit non-zero.
    try:
        run_review(github=github, engine=engine, cfg=cfg, dry_run=False, ctx=ctx)
    except Exception as exc:
        _post_failure(github, exc)
        raise click.ClickException(f"review failed: {exc}") from exc
    finally:
        # Deferred deliberately: posting a comment fires an issue_comment
        # workflow run, and a consumer whose concurrency group isn't
        # discriminated by event name puts that run in the same group as this
        # review — cancel-in-progress then kills the review that posted the
        # comment. With the review already on the PR, such a cancellation is
        # harmless. In a `finally` so a failed review still gets its extras,
        # exactly as when they ran first.
        if ctx is not None:
            _post_extras(github, provider, cfg, ctx, describe=describe, diagram=diagram)

    if runtime.profile:
        click.echo(profiler.render())


def execute_comment(event: dict[str, Any], cfg: ReviewConfig, runtime: RuntimeOptions) -> None:
    """Route an issue_comment event's slash command to the engine/provider.

    Shared by the ``comment`` command and the ``action`` entrypoint. ``runtime``
    supplies api_key/api_base/fallback_model; the PR URL is derived here.
    """
    from lgtmaybe.cli.slash import dispatch, parse_command

    parsed = parse_command(event.get("comment", {}).get("body", ""))
    if parsed is None:
        click.echo("No lgtmaybe slash command found; ignoring.")
        return

    issue = event.get("issue", {})
    if "pull_request" not in issue:
        click.echo("Comment is not on a pull request; ignoring.")
        return

    try:
        repo = event["repository"]["full_name"]
        pr_number = issue["number"]
    except (KeyError, TypeError) as exc:
        raise click.ClickException(f"event payload missing required field: {exc}") from exc
    runtime = replace(runtime, pr_url=f"https://github.com/{repo}/pull/{pr_number}")

    try:
        github, engine, provider_client = build_review_context(cfg, runtime)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        dispatch(parsed, github=github, engine=engine, provider=provider_client, cfg=cfg)
    except Exception as exc:
        _post_failure(github, exc)
        raise click.ClickException(f"/{parsed.name} failed: {exc}") from exc


def execute_review_reply(event: dict[str, Any], cfg: ReviewConfig, runtime: RuntimeOptions) -> None:
    """Answer a PR author's reply inside a finding conversation lgtmaybe opened.

    Handles a ``pull_request_review_comment`` event. It acts **only** when every
    loop-safety condition holds — ``answer_replies`` on, a freshly *created*
    reply (``in_reply_to_id`` present), from a non-bot author, whose thread's
    root comment carries our finding marker — and answers in that same thread,
    using the finding and its surrounding diff hunk as context. Every other case
    returns without posting, so the reviewer never answers its own replies in a
    loop. The reply body is untrusted input (redacted + delimiter-neutralised
    before it reaches the model); the model's answer is fence-defanged before it
    is posted.
    """
    from lgtmaybe.cli.slash import _answer_reply
    from lgtmaybe.github.rest_gateway import _defang_fences

    # Loop-safety gates — all checked before any network call. A failure here is
    # a silent no-op: the event simply isn't one we answer.
    if not cfg.answer_replies:
        click.echo("answer_replies is off; ignoring review comment.")
        return
    if event.get("action") != "created":
        return
    comment = event.get("comment") or {}
    in_reply_to = comment.get("in_reply_to_id")
    if not in_reply_to:
        click.echo("Review comment is not a reply; ignoring.")
        return
    if (comment.get("user") or {}).get("type") == "Bot":
        # Never answer a bot (including ourselves) — that is the reply loop.
        return

    try:
        repo = event["repository"]["full_name"]
        pr_number = event["pull_request"]["number"]
    except (KeyError, TypeError) as exc:
        raise click.ClickException(f"event payload missing required field: {exc}") from exc
    runtime = replace(runtime, pr_url=f"https://github.com/{repo}/pull/{pr_number}")

    try:
        github, _engine, provider = build_review_context(cfg, runtime)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        thread = github.find_review_thread(int(in_reply_to))
        if thread is None:
            click.echo("Reply is not in a review thread we can resolve; ignoring.")
            return
        thread_id, root_body = thread
        finding = _finding_from_comment(root_body)
        if finding is None:
            # The thread's root is not one of ours — do not answer.
            click.echo("Reply thread was not opened by lgtmaybe; ignoring.")
            return
        answer = _answer_reply(
            provider,
            cfg,
            finding=finding,
            hunk=_reply_hunk(github, comment),
            reply=comment.get("body") or "",
        )
        github.reply_in_thread(thread_id, _defang_fences(answer))
    except Exception as exc:
        _post_failure(github, exc)
        raise click.ClickException(f"answering the review reply failed: {exc}") from exc


def _finding_from_comment(root_body: str) -> str | None:
    """The visible finding text of a lgtmaybe inline comment, or None if not ours.

    Our inline finding comments carry a hidden ``lgtmaybe-finding`` marker; a
    thread whose root lacks it was not opened by us and must not be answered.
    The fingerprint is a one-way hash, so the finding is recovered from the
    visible title/body text (marker stripped), never by reversing the hash.
    """
    from lgtmaybe.github.rest_gateway import _FINDING_MARKER

    if _FINDING_MARKER.search(root_body) is None:
        return None
    return _FINDING_MARKER.sub("", root_body).strip()


def _reply_hunk(github: GitHubGateway, comment: dict[str, Any]) -> str:
    """The diff hunk covering the replied-to comment's line, or "" if none.

    Reads the comment's ``path``/``line`` (falling back to ``original_line`` for
    an outdated position) off the review-comment payload and slices the covering
    hunk out of the already-fetched PR diff — grounding context for the answer.
    """
    path = comment.get("path") or ""
    line = comment.get("line") or comment.get("original_line") or 0
    if not path or not line:
        return ""
    hunk = hunk_for_line(github.get_pr_context().diff, path, int(line))
    return hunk or ""


def _post_failure(github: GitHubGateway, exc: Exception) -> None:
    """Post a short failure notice to the PR; never raise from here."""
    notice = f"⚠️ lgtmaybe review failed: {exc}"
    try:
        # run_review may have stamped the reviewed watermark before the post
        # failed — clear it so the failure notice doesn't carry it and the next
        # incremental run re-reviews the commits whose findings never posted.
        mark_reviewed = getattr(github, "mark_reviewed", None)
        if mark_reviewed is not None:
            mark_reviewed(None)
        github.post_review([], notice)
    except Exception:
        # Posting the failure notice itself failed — nothing more we can do;
        # the original error is still surfaced by the caller's ClickException.
        pass
    # Same visibility problem as an incomplete run, in its worst form: on a
    # re-run the write above only edits the older review's body. Attempted in
    # its OWN try, because the body update is the write most likely to fail for
    # the very reason the review did (bad token, rate limit, deleted review) —
    # sharing a try would make the visible disclosure contingent on the
    # invisible one, which is the bug being fixed.
    try:
        github.post_issue_comment(notice)
    except Exception:
        # Best-effort, like the notice above: this path must never raise.
        pass


def pr_url_from_event(event: dict[str, Any]) -> str:
    """Build the PR URL from a pull_request(_target) event payload.

    Only github.com is supported end to end — the URL parser and the REST
    gateway both speak to api.github.com.
    """
    try:
        repo = event["repository"]["full_name"]
        number = event["pull_request"]["number"]
    except (KeyError, TypeError) as exc:
        raise click.ClickException(f"event payload missing required field: {exc}") from exc
    return f"https://github.com/{repo}/pull/{number}"


def action_inputs() -> dict[str, str | None]:
    """Read the action's declared inputs from the ``INPUT_*`` env vars.

    GitHub sets ``INPUT_<NAME>`` for each input of a container action; empty
    strings (an unset optional input) are normalised to ``None``.
    """

    def get(name: str) -> str | None:
        value = os.environ.get(f"INPUT_{name}")
        return value or None

    return {
        "provider": get("PROVIDER"),
        "model": get("MODEL"),
        "preset": get("PRESET"),
        "fallback_model": get("FALLBACK_MODEL"),
        "reflect_model": get("REFLECT_MODEL"),
        "language": get("LANGUAGE"),
        "triage_model": get("TRIAGE_MODEL"),
        "api_key": get("API_KEY"),
        "api_base": get("API_BASE"),
        "timeout": get("TIMEOUT"),
        "max_review_seconds": get("MAX_REVIEW_SECONDS"),
        "temperature": get("TEMPERATURE"),
        "num_ctx": get("NUM_CTX"),
        "max_input_tokens": get("MAX_INPUT_TOKENS"),
        "max_tokens": get("MAX_TOKENS"),
        "max_concurrency": get("MAX_CONCURRENCY"),
        "resolve_fixed": get("RESOLVE_FIXED"),
        "recursive": get("RECURSIVE"),
        "structured_output": get("STRUCTURED_OUTPUT"),
        "symbol_resolution": get("SYMBOL_RESOLUTION"),
        "prompt_cache": get("PROMPT_CACHE"),
        "incremental": get("INCREMENTAL"),
        "static_analysis": get("STATIC_ANALYSIS"),
        "auto_describe": get("AUTO_DESCRIBE"),
        "auto_diagram": get("AUTO_DIAGRAM"),
        "answer_replies": get("ANSWER_REPLIES"),
        "pr_labels": get("PR_LABELS"),
        "learn_feedback": get("LEARN_FEEDBACK"),
        "fail_on": get("FAIL_ON"),
        "profile": get("PROFILE"),
        "config_path": get("CONFIG_PATH"),
    }


_EPILOG = """\
\b
Examples:
  lgtmaybe review                          Review your branch vs the default branch
  lgtmaybe review --working                Include uncommitted edits too
  lgtmaybe review --provider ollama --model qwen3:27b
  lgtmaybe review --format json            Machine-readable findings
  lgtmaybe config init                     One-time provider/model setup
  lgtmaybe help COMMAND                    Detailed help for any command
\b
Docs: https://lgtmaybe.coles.codes/
"""


def _utf8_stdio() -> None:
    """Keep redirected Windows output safe for summaries containing Unicode."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")


@click.group(epilog=_EPILOG)
def main() -> None:
    """lgtmaybe — provider-agnostic PR reviewer."""
    _utf8_stdio()


@main.group(name="config")
def config_cmd() -> None:
    """Manage the user-level config (set provider/model/api_base once, reuse everywhere)."""


# Importing the commands module registers every command onto the groups above.
# Done last so the logic functions the commands call are already defined.
from lgtmaybe.cli import commands as _commands  # noqa: E402,F401

__all__ = [
    "RuntimeOptions",
    "action_inputs",
    "build_adapters",
    "build_provider_engine",
    "build_review_context",
    "config_cmd",
    "execute_comment",
    "execute_describe",
    "execute_local_review",
    "execute_review",
    "execute_review_reply",
    "main",
    "parse_pr_url",
    "pr_url_from_event",
    "render_findings",
    "resolve_auto_incremental",
    "run_describe",
    "run_review",
    "should_auto_describe",
]
