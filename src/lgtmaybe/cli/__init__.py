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

import json
import os
import signal
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import click

from lgtmaybe.cli.render import flatten_details, render_findings
from lgtmaybe.core.diffparse import split_by_file
from lgtmaybe.core.forge import Forge, PRLocator, token_env_var
from lgtmaybe.core.forge import parse_pr_url as locate_pr
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ReviewConfig,
    ReviewFinding,
    ReviewPreset,
    Severity,
)
from lgtmaybe.core.ports import (
    ProviderClient,
    ReviewEngine,
    ReviewGateway,
    SupportsBaseCheckout,
    SupportsFileContents,
)
from lgtmaybe.core.version import package_version
from lgtmaybe.engine import (
    INCOMPLETE_MARKER,
    FileFetcher,
    LLMReviewEngine,
    SymbolResolver,
    build_symbol_resolver,
    concurrency_cap,
    request_interrupt,
)
from lgtmaybe.engine.profiling import profiler
from lgtmaybe.gitea import GiteaGateway
from lgtmaybe.github import RestGitHubGateway
from lgtmaybe.gitlab import GitLabGateway
from lgtmaybe.local import local_file_reader, local_pr_context, local_repo_root
from lgtmaybe.providers.credentials import resolve_credentials
from lgtmaybe.providers.factory import (
    build_provider,
    cheaper_reflect_sibling,
    default_timeout_for,
    resolve_max_tokens,
)


@dataclass(frozen=True)
class RuntimeOptions:
    """Call-time options resolved from CLI flags or GitHub Action inputs."""

    api_key: str | None = None
    api_base: str | None = None
    fallback_model: str | None = None
    pr_url: str | None = None
    profile: bool = False
    # Where to write the machine-readable profile, if anywhere. A PATH rather
    # than a stream: stdout already carries the findings under --json/--agent,
    # and a file collides with nothing whatever the output format is.
    profile_json: Path | None = None


_log = get_logger(__name__)


def _should_auto_post(enabled: bool, event_action: str) -> bool:
    """Gate an automatic extra to newly opened or reopened pull requests."""
    return enabled and event_action in ("opened", "reopened")


def should_auto_describe(cfg: ReviewConfig, *, event_action: str) -> bool:
    """Whether the action run should post the structured description first."""
    return _should_auto_post(cfg.auto_describe, event_action)


def should_auto_diagram(cfg: ReviewConfig, *, event_action: str) -> bool:
    """Whether the action run should post or refresh the change diagram."""
    return cfg.auto_diagram and event_action in ("opened", "reopened", "synchronize")


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
    post = getattr(github, "post_describe_comment", None)
    if post is not None:
        post(body)
        return
    post_comment = getattr(github, "post_issue_comment", None)
    if post_comment is not None:
        post_comment(body)


def run_diagram(
    github: ReviewGateway,
    provider: ProviderClient,
    cfg: ReviewConfig,
    ctx: PRContext | None = None,
    *,
    completed_sha: str | None = None,
) -> None:
    """Build the change diagram and post it idempotently."""
    from lgtmaybe.engine.diagram import build_diagram

    body = build_diagram(github.get_pr_context() if ctx is None else ctx, cfg, provider)
    post = getattr(github, "post_diagram_comment", None)
    if post is not None:
        post(body, completed_sha=completed_sha)
        return
    if completed_sha is not None:
        raise RuntimeError("the GitHub gateway cannot persist a diagram completion marker")
    post_comment = getattr(github, "post_issue_comment", None)
    if post_comment is not None:
        post_comment(body)
        return
    raise RuntimeError("the GitHub gateway cannot post a diagram")


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


# Which forges lgtmaybe can build a gateway for. A forge that parses but is not
# in here is recognised-but-unimplemented, which earns a different (and much more
# useful) error than an unparseable URL.
_GATEWAY_BUILDERS: dict[Forge, Callable[[PRLocator, str, ReviewConfig], ReviewGateway]] = {
    Forge.github: lambda located, token, cfg: RestGitHubGateway(
        repo=located.repo,
        pr_number=located.number,
        token=token,
        marker_key=f"{cfg.provider}/{cfg.model}",
        resolve_fixed=cfg.resolve_fixed,
    ),
    Forge.gitlab: lambda located, token, cfg: GitLabGateway(
        host=located.host,
        repo=located.repo,
        pr_number=located.number,
        token=token,
        marker_key=f"{cfg.provider}/{cfg.model}",
        resolve_fixed=cfg.resolve_fixed,
        scheme=located.scheme,
    ),
    Forge.gitea: lambda located, token, cfg: GiteaGateway(
        host=located.host,
        repo=located.repo,
        pr_number=located.number,
        token=token,
        marker_key=f"{cfg.provider}/{cfg.model}",
        scheme=located.scheme,
    ),
}


def gateway_builder(
    forge: Forge,
) -> Callable[[PRLocator, str, ReviewConfig], ReviewGateway] | None:
    """The gateway factory for ``forge``, or None when it is not implemented yet."""
    return _GATEWAY_BUILDERS.get(forge)


def _bounded_default(cfg: ReviewConfig, concurrency: int) -> int:
    """The provider default, widened for queue time, then bounded by the run.

    Six workers against the generous local default is three hours of per-call
    budget, and the whole-review deadline cannot take it back: that deadline only
    skips calls that have not *started*, and a fan-out narrower than the pool
    starts all of its calls at once. So the clamp happens here instead.

    What it bounds is the *budget*, not the wall clock, and only down to the
    provider's own default. Two things keep it short of "no call outlives the
    review". The deadline is a start gate, so a call beginning just inside it
    still runs its full budget afterwards; and the floor below wins over a
    deadline set beneath it, so ``max_review_seconds: 600`` still resolves 1800.
    What the clamp buys, when the deadline is at or above that floor: a
    pathological run is capped at roughly twice the deadline where unclamped it
    is four times. Below the floor it buys nothing, because the floor wins —
    ``max_review_seconds: 600`` resolves 1800, and the same run is four times the
    deadline again. Making the wall clock itself the bound needs a per-call
    budget computed from the deadline *remaining* at call time, which the port
    does not currently carry.

    Bounded in both directions. ``max_review_seconds: 0`` means "no deadline",
    not "zero seconds"; and the clamp may only take back what the scaling added,
    never drag a call below the provider default it would have had at width one.
    """
    scaled = default_timeout_for(cfg.provider, concurrency=concurrency)
    if not cfg.max_review_seconds:
        return scaled
    return max(default_timeout_for(cfg.provider), min(scaled, cfg.max_review_seconds))


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
    # The output cap. Forwarded verbatim — including the 0 that means "uncapped"
    # — because the factory is what interprets it: unset resolves to a
    # provider-aware default there (a finite ceiling for ollama, none for the
    # hosted routes), exactly as the timeout does. Not gated on the provider;
    # every route honours it, since litellm normalises the param.
    if cfg.max_tokens is not None:
        extra["max_tokens"] = cfg.max_tokens
    # Reasoning budget, sent only when asked for. litellm normalises the param
    # across the routes that support one, and a route without a reasoning
    # channel never sees it — so, like max_tokens, this is not gated on provider.
    if cfg.reasoning_effort is not None:
        extra["reasoning_effort"] = cfg.reasoning_effort
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
    #
    # The width is part of the budget on a local endpoint. lgtmaybe issues the
    # whole fan-out at once, and a single-slot server (ollama's default) makes
    # all but the first wait their turn with their timeout clocks already
    # running — so the default is scaled by the width there, and only there.
    # This is the one place that knows both numbers.
    #
    # The output ceiling rides along for the same reason, and it needs the naming
    # more than the timeout does: a truncated lens reports "response truncated at
    # the 8192-token `max_tokens` ceiling", which reads as the user's own setting
    # even when nobody set it. Announced up front, `uncapped` included — that is
    # the state that puts a run back on the timeout for its only stop.
    concurrency = concurrency_cap(cfg)
    effective_timeout = (
        cfg.timeout if cfg.timeout is not None else _bounded_default(cfg, concurrency)
    )
    ceiling = resolve_max_tokens(cfg.provider, cfg.max_tokens)
    _log.info(
        "per-call budget resolved",
        extra={
            "lgtmaybe_version": package_version(),
            "provider": cfg.provider.value,
            "timeout_s": effective_timeout,
            "timeout_source": "configured" if cfg.timeout is not None else "provider default",
            "concurrency": concurrency,
            "max_tokens": ceiling,
            "max_tokens_source": (
                "uncapped"
                if ceiling is None
                else "configured"
                if cfg.max_tokens
                else "provider default"
            ),
        },
    )
    provider = build_provider(
        cfg.provider,
        cfg.model,
        api_key=auth.api_key,
        api_base=auth.api_base,
        azure_ad_token=auth.azure_ad_token,
        # Same precedence as `api_base` above: the invocation (CLI flag / Action
        # input) overrides what the repo's config asked for.
        fallback_model=runtime.fallback_model or cfg.fallback_model,
        # The resolved value, not cfg.timeout — the widening and its bound are
        # decided above, and passing the raw setting would have the factory
        # resolve a second, unbounded number that the log above then misreports.
        timeout=effective_timeout,
        temperature=cfg.temperature,
        **extra,
    )
    engine = LLMReviewEngine(provider, fetch_file=fetch_file, resolve_symbol=resolve_symbol)
    return engine, provider


def build_review_context(
    cfg: ReviewConfig, runtime: RuntimeOptions
) -> tuple[ReviewGateway, LLMReviewEngine, ProviderClient]:
    """Construct the gateway, engine, and provider from config + runtime.

    Builds the model side via ``build_provider_engine`` and points the gateway
    for the URL's forge at the parsed change request. Raises ValueError with an
    actionable message when the forge is unsupported or its token is missing.
    The provider is returned too so slash commands (/ask, /describe) can use it
    directly.
    """
    if runtime.pr_url is None:
        raise ValueError("a PR URL is required to build the review context")
    located = locate_pr(runtime.pr_url)

    build_gateway = gateway_builder(located.forge)
    if build_gateway is None:
        raise ValueError(
            f"{located.forge} is not supported yet — lgtmaybe can only post reviews to "
            f"{', '.join(sorted(_GATEWAY_BUILDERS))} so far."
        )

    token_var = token_env_var(located.forge)
    token = os.environ.get(token_var)
    if not token:
        raise ValueError(
            f"{token_var} is required to fetch the change and post the review. "
            "Set it in the environment (the GitHub Action provides it automatically)."
        )

    github = build_gateway(located, token, cfg)
    # Wire the gateway's read-only file fetcher so the reflection pass can resolve a
    # deferred verdict (fetch a referenced file the auditor needs) instead of
    # dropping the finding. Read-only API fetch — never a checkout (fork-safe).
    # Cross-file symbol resolution: when the auditor defers on a symbol (not a path),
    # ast-grep finds its defining file in a lazily-cloned checkout of the trusted base
    # branch. Read-only (clone + parse, never execute the PR head), and a no-op when
    # ast-grep is absent. The clone happens only on the first symbol deferral.
    # Both reads are optional capabilities: a forge adapter that cannot serve
    # file text still reviews the diff, it just cannot resolve a deferral.
    resolve_symbol = (
        build_symbol_resolver(github.base_checkout_root)
        if cfg.symbol_resolution and isinstance(github, SupportsBaseCheckout)
        else None
    )
    fetch_file = github.get_file_contents if isinstance(github, SupportsFileContents) else None
    engine, provider = build_provider_engine(
        cfg, runtime, fetch_file=fetch_file, resolve_symbol=resolve_symbol
    )
    return github, engine, provider


def _apply_learned_feedback(github: ReviewGateway, ctx: PRContext, cfg: ReviewConfig) -> PRContext:
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
    ``diagram_required`` makes that current-head diagram part of completion.

    With ``cfg.incremental`` on and a gateway that supports it, only the diff
    since the last completed head is reviewed while earlier findings are
    validated. A completed same-head run is a no-op; every degraded case
    (first review, force-push, compare failure, incomplete state, or a gateway
    without the adapter methods) falls back to a full review.
    """
    if ctx is None:
        # Dependency manifests are fetched only when a scanner will read them —
        # an adapter-only method, like set_incremental_scope, so a gateway
        # implementing just the frozen port still works.
        want_manifests = getattr(github, "set_scan_manifests", None)
        if callable(want_manifests):
            want_manifests(cfg.static_analysis.enabled)
        ctx = github.get_pr_context()
    review_ctx, incremental_since, already_complete = _incremental_context(
        github, ctx, cfg, diagram_required=diagram_required
    )
    if already_complete:
        return [], f"Head {ctx.head_sha[:7]} is already complete; nothing changed."
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
            f"\n\n_Incremental review of the changes since {incremental_since[:7]} — "
            "earlier findings stay open until fixed._"
        )
        if validation_summary:
            summary += f"\n\n{validation_summary}"

    if not dry_run:
        # Prepare the watermark for the review body. This is only in-memory
        # adapter state until post_review succeeds, so an interruption here
        # cannot remotely complete the head.
        complete = INCOMPLETE_MARKER not in summary
        mark_reviewed = getattr(github, "mark_reviewed", None)
        if mark_reviewed is not None:
            mark_reviewed(ctx.head_sha if complete else None)
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
        if diagram_required and complete:
            if provider is None:
                raise ValueError("a provider is required for automatic diagram completion")
            run_diagram(github, provider, cfg, ctx=ctx, completed_sha=ctx.head_sha)

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
    github: ReviewGateway,
    ctx: PRContext,
    cfg: ReviewConfig,
    *,
    diagram_required: bool = False,
) -> tuple[PRContext, str | None, bool]:
    """Return ``(context, completed_sha, already_complete)`` for this run.

    Incremental only when ``cfg.incremental`` is truthy (None = auto resolves
    upstream, and unresolved auto means full), the gateway has the adapter
    methods (``last_completed_sha`` / ``compare_diff`` — fakes and the frozen
    port don't), a completion watermark exists, the head moved since, and the
    compare yields a usable increment. A matching head sets the final flag;
    every degraded case returns the full context with no completed SHA.
    """
    if not cfg.incremental:
        return ctx, None, False
    last_completed_sha = getattr(github, "last_completed_sha", None)
    last_reviewed_sha = getattr(github, "last_reviewed_sha", None)
    compare_diff = getattr(github, "compare_diff", None)
    if compare_diff is None or (last_completed_sha is None and last_reviewed_sha is None):
        return ctx, None, False
    if last_completed_sha is not None:
        last_sha = last_completed_sha(diagram_required=diagram_required)
    elif last_reviewed_sha is not None:
        last_sha = last_reviewed_sha()
    else:  # guarded above; keeps the optional callable narrowed for type-checkers
        return ctx, None, False
    if not last_sha:
        return ctx, None, False
    if last_sha == ctx.head_sha:
        return ctx, None, True
    increment = compare_diff(last_sha, ctx.head_sha)
    if not increment or not increment.strip():
        return ctx, None, False
    _log.info(
        "incremental review",
        extra={"since": last_sha, "head": ctx.head_sha},
    )
    return ctx.model_copy(update={"diff": increment}), last_sha, False


def _diff_paths(diff: str) -> set[str]:
    """The file paths named by a unified diff's ``diff --git`` headers."""
    return {path for path, _patch in split_by_file(diff, []) if path != "unknown"}


def _validate_prior_findings(
    github: ReviewGateway,
    provider: ProviderClient | None,
    cfg: ReviewConfig,
    ctx: PRContext,
    current_findings: list[ReviewFinding],
) -> str:
    """Validate active prior findings and install the explicit resolve allowlist."""
    list_active = getattr(github, "list_active_findings", None)
    set_fixed = getattr(github, "set_validated_fixed_threads", None)
    if list_active is None or set_fixed is None:
        return ""
    try:
        active = list_active()
    except Exception as exc:  # noqa: BLE001 - failure leaves every thread open
        _log.warning("reading active findings for validation failed: %s", exc)
        set_fixed(set())
        return "_Follow-up validation was unavailable; earlier findings remain open._"
    if not active:
        set_fixed(set())
        return ""

    from lgtmaybe.core.findings import finding_fingerprint, finding_identity
    from lgtmaybe.core.models import FindingValidation, FindingValidationStatus
    from lgtmaybe.engine.validate import validate_findings

    current_keys = {
        key
        for finding in current_findings
        for key in (finding_fingerprint(finding.path, finding.title), finding_identity(finding))
    }
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
            reason="the model reported a fix but GitHub does not mark the finding outdated",
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
        resolve_symbol = build_symbol_resolver(local_repo_root) if cfg.symbol_resolution else None
        engine, _provider = build_provider_engine(
            cfg, runtime, fetch_file=fetch_file, resolve_symbol=resolve_symbol
        )
        ctx = local_pr_context(base=base, working=working, uncommitted=uncommitted)
        findings, summary = engine.review(ctx, cfg)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    if fmt == "json" and INCOMPLETE_MARKER in summary:
        click.echo(summary.replace(INCOMPLETE_MARKER, "").strip(), err=True)
    click.echo(render_findings(findings, summary, fmt=fmt))
    _write_profile_json(runtime)
    if runtime.profile:
        # stdout carries the deliverable, so the table only shares it when the
        # deliverable is for a human. Under --json/--agent stdout is a machine
        # channel and the table made it unparseable — `--json --profile | jq`
        # simply failed. Same rule the footer below already follows.
        click.echo(profiler.render(), err=fmt != "human")
    elif profiler.total_tokens():
        # The meter. A local review is the one that runs dozens of times a day
        # against a metered API, so what it spent is reported by default rather
        # than only under --profile (whose table already ends with this line —
        # hence the elif). stderr keeps --json / --agent output pipeable.
        click.echo(profiler.render_total(), err=True)


def _write_profile_json(runtime: RuntimeOptions) -> None:
    """Write the structured profile, when one was asked for.

    Best-effort by design: a review that produced findings must not fail because
    a diagnostic file could not be written.
    """
    if runtime.profile_json is None:
        return
    try:
        runtime.profile_json.write_text(json.dumps(profiler.as_dict(), indent=2), encoding="utf-8")
    except OSError as exc:
        _log.warning("could not write the profile json", extra={"error": str(exc)})


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
    Mermaid source (paste it into GitHub to render) plus the text rendering,
    which is what actually shows in a terminal — with the comment's collapsible
    wrappers flattened, since a terminal renders no HTML.
    """
    from lgtmaybe.engine.diagram import build_diagram

    try:
        _engine, provider = build_provider_engine(cfg, runtime)
        ctx = local_pr_context(base=base, working=working, uncommitted=uncommitted)
        body = build_diagram(ctx, cfg, provider)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(flatten_details(body))


def _build_context_or_fail(
    cfg: ReviewConfig, runtime: RuntimeOptions
) -> tuple[ReviewGateway, ReviewEngine, ProviderClient]:
    """Build the adapter set, surfacing construction failures as a CLI error.

    Adapter construction can fail before we have any way to post (bad URL,
    missing token/credentials), so it is the one failure that cannot be
    reported back to the PR — it becomes a clean ``ClickException`` instead.
    """
    try:
        return build_review_context(cfg, runtime)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc


def _post_extras(
    github: ReviewGateway,
    provider: ProviderClient,
    cfg: ReviewConfig,
    ctx: PRContext,
    *,
    describe: bool,
) -> None:
    """Post the best-effort automatic description over a prefetched context.

    Automatic descriptions remain best-effort. Required automatic diagrams are
    posted by ``run_review`` and never enter this helper.
    """
    if describe:
        try:
            run_describe(github, provider, cfg, ctx=ctx)
        except Exception:
            _log.warning("auto-describe failed — continuing without it", exc_info=True)


def execute_review(
    cfg: ReviewConfig,
    runtime: RuntimeOptions,
    *,
    describe: bool = False,
    diagram: bool = False,
) -> None:
    """Build adapters, run the review, and surface failures back to the PR.

    Shared by the ``review`` command and Action entrypoint. Automatic
    descriptions are best-effort after review; an automatic diagram is a
    required completion step inside ``run_review``. Both reuse one adapter set
    and the prefetched O(files) PR context.
    """
    profiler.reset()
    github, engine, provider = _build_context_or_fail(cfg, runtime)

    ctx: PRContext | None = None
    if describe or diagram:
        want_manifests = getattr(github, "set_scan_manifests", None)
        if callable(want_manifests):
            want_manifests(cfg.static_analysis.enabled)
        # A failed prefetch means run_review fetches and surfaces the failure
        # itself. Fetching here lets the review, required diagram, and optional
        # description share one current-head context.
        try:
            ctx = github.get_pr_context()
        except Exception:
            _log.warning("PR context prefetch failed — extras skipped", exc_info=True)

    # From here we have a gateway, so any failure is surfaced back to the PR as
    # a short comment rather than failing silently — then we exit non-zero.
    try:
        run_review(
            github=github,
            engine=engine,
            cfg=cfg,
            dry_run=False,
            ctx=ctx,
            provider=provider,
            diagram_required=diagram,
        )
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
            _post_extras(github, provider, cfg, ctx, describe=describe)

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
    runtime = replace(runtime, pr_url=_change_url(repo, pr_number))

    github, engine, provider = _build_context_or_fail(cfg, runtime)

    try:
        dispatch(parsed, github=github, engine=engine, provider=provider, cfg=cfg)
    except Exception as exc:
        _post_failure(github, exc)
        raise click.ClickException(f"/{parsed.name} failed: {exc}") from exc


def _post_failure(github: ReviewGateway, exc: Exception) -> None:
    """Post a short failure notice to the PR; never raise from here."""
    # Name the version: unlike the summary line this notice carries no model,
    # so without it a failure report says nothing about what was running.
    notice = f"⚠️ lgtmaybe review failed: {exc}\n\n_lgtmaybe {package_version()}_"
    try:
        # run_review may have prepared the reviewed watermark before the post
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


def _change_url(repo: str, number: int) -> str:
    """Build the change-request URL for whichever host is running this job.

    Gitea Actions reimplements GitHub Actions' env contract — same
    ``GITHUB_EVENT_NAME``, same ``GITHUB_EVENT_PATH``, same ``INPUT_*`` — so the
    entrypoint needs no forge switch of its own. The one variable that does
    differ are ``GITHUB_SERVER_URL`` and ``GITHUB_API_URL``. Together they
    distinguish Gitea from unsupported GitHub Enterprise Server. Absent (or
    github.com), the URL is unchanged from before.

    The path segment is what ``core.forge`` discriminates on, so it has to match
    the host's own convention: GitHub singularises ``pull``, Gitea pluralises it.
    """
    server = os.environ.get("GITHUB_SERVER_URL", "").rstrip("/")
    if not server or server == "https://github.com":
        return f"https://github.com/{repo}/pull/{number}"
    if os.environ.get("GITHUB_API_URL", "").rstrip("/").endswith("/api/v3"):
        raise click.ClickException(
            "GitHub Enterprise Server is not supported; run lgtmaybe on GitHub.com, "
            "GitLab, or Gitea."
        )
    return f"{server}/{repo}/pulls/{number}"


def mr_url_from_ci_env() -> str:
    """Build the merge request URL from GitLab CI's predefined variables.

    GitLab CI has no event payload file and no ``INPUT_*`` convention, so this
    is the one entrypoint that cannot be shared with the GitHub Actions path.
    Everything downstream is identical: the URL goes through the same locator
    and the same gateway registry.

    Raises with the variable's own name when one is missing — the usual cause is
    a job running on a branch pipeline rather than a merge request one, and
    naming ``CI_MERGE_REQUEST_IID`` points straight at the ``rules:`` fix.
    """
    host = os.environ.get("CI_SERVER_HOST") or ""
    server = os.environ.get("CI_SERVER_URL", "").rstrip("/") or (host and f"https://{host}")
    if not server:
        raise click.ClickException(
            "CI_SERVER_HOST is not set — lgtmaybe cannot tell which GitLab to post to."
        )
    project = os.environ.get("CI_MERGE_REQUEST_PROJECT_PATH") or os.environ.get("CI_PROJECT_PATH")
    if not project:
        raise click.ClickException("CI_PROJECT_PATH is not set — no project to review.")
    iid = os.environ.get("CI_MERGE_REQUEST_IID")
    if not iid:
        raise click.ClickException(
            "CI_MERGE_REQUEST_IID is not set — this pipeline is not for a merge request. "
            'Run the job with `rules: - if: $CI_PIPELINE_SOURCE == "merge_request_event"`.'
        )
    return f"{server}/{project}/-/merge_requests/{iid}"


def pr_url_from_event(event: dict[str, Any]) -> str:
    """Build the change-request URL from a pull_request(_target) event payload."""
    try:
        repo = event["repository"]["full_name"]
        number = event["pull_request"]["number"]
    except (KeyError, TypeError) as exc:
        raise click.ClickException(f"event payload missing required field: {exc}") from exc
    return _change_url(repo, number)


#: Inputs ``action()`` handles itself rather than passing to ``ReviewConfig``:
#: credentials and per-run options that live on ``RuntimeOptions``, the nested
#: static-analysis toggle, and the config file path.
_RUNTIME_INPUTS = frozenset(
    {"api_key", "api_base", "fallback_model", "profile", "static_analysis", "config_path"}
)

#: Config fields intentionally available only through the config file or local
#: CLI. Everything else is an Action input; action.yml parity tests make a newly
#: derived name fail loudly until its declaration and environment mapping exist.
_ACTION_CONFIG_EXCLUSIONS = frozenset(
    {
        "categories",
        "context_lines",
        "directory_rules",
        "exclude_paths",
        "extra_lenses",
        "finding_rules",
        "function_context",
        "ignore_fingerprints",
        "include_paths",
        "max_file_diff_lines",
        "max_files",
        "min_confidence",
        "min_severity",
        "reflect",
        "spec_paths",
        "summary_template",
        "unanchored_min_severity",
    }
)
_ACTION_INPUTS = tuple(
    sorted(set(ReviewConfig.model_fields) - _ACTION_CONFIG_EXCLUSIONS | _RUNTIME_INPUTS)
)


def action_inputs() -> dict[str, str | None]:
    """Read the action's declared inputs from the ``INPUT_*`` env vars.

    GitHub sets ``INPUT_<NAME>`` for each input of a container action; empty
    strings (an unset optional input) are normalised to ``None``.
    """
    return {name: os.environ.get(f"INPUT_{name.upper()}") or None for name in _ACTION_INPUTS}


_EPILOG = """\
\b
Examples:
  lgtmaybe review                          Review your branch vs the default branch
  lgtmaybe review --working                Include uncommitted edits too
  lgtmaybe review --provider ollama --model qwen3:27b
  lgtmaybe review --format json            Machine-readable findings
  lgtmaybe diagram                         Diagram the components you touched
  lgtmaybe config init                     One-time provider/model setup
  lgtmaybe review --help                   Detailed help for a command
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


@contextmanager
def graceful_interrupt() -> Iterator[None]:
    """Turn the FIRST SIGINT/SIGTERM into the engine's partial-results wind-down.

    A CI job that blows its `timeout-minutes`, or a run cancelled by
    `cancel-in-progress`, is signalled before it is killed. Ignoring that signal
    is why an over-running review posts *nothing* — no findings, no failure
    comment. The handler sets the same state the `max_review_seconds` deadline
    sets, so queued model calls are skipped and the review posts what it already
    has (see `engine.request_interrupt`).

    Installed here, in the CLI entrypoint, and never at import time: importing
    lgtmaybe as a library must not hijack a host application's handlers. The
    previous handler is restored on the way out *and* on the first signal — so a
    second signal takes its normal course and the process is still killable if
    the wind-down itself hangs. Off the main thread (where `signal.signal`
    raises) and on a platform missing a signal, this is a no-op.
    """
    previous: dict[int, Any] = {}

    def _restore() -> None:
        for sig in list(previous):
            handler = previous.pop(sig, None)
            if handler is not None:
                with suppress(ValueError, OSError):
                    signal.signal(sig, handler)

    def _on_signal(signum: int, _frame: Any) -> None:
        _restore()
        _log.warning(
            "interrupted — winding down and posting partial results",
            extra={"signal": signum},
        )
        request_interrupt()

    try:
        for name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is not None:
                previous[sig] = signal.signal(sig, _on_signal)
    except (ValueError, OSError):
        # Not the main thread, or the platform won't take this handler: degrade
        # to the process's normal termination behaviour rather than fail.
        _restore()
    try:
        yield
    finally:
        _restore()


def _print_version(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    """Print `lgtmaybe <version>` and exit — the eager `--version` callback.

    Written by hand rather than with ``click.version_option`` because both of
    that helper's modes get an edge case wrong here. Passing a resolved version
    would read the distribution metadata at import time, on every invocation
    that never asks for it; passing ``package_name`` makes click do the read
    itself and raise ``RuntimeError`` when there is no dist-info, so the flag
    would CRASH in a source checkout. ``package_version`` already answers
    "unknown" there, and a diagnostic that fails is worse than one that hedges.

    The line is deliberately plain: a benchmark runner records the executable it
    ran by parsing this, so it names the program (never click's ``main``) in one
    stable shape.
    """
    if not value or ctx.resilient_parsing:
        return
    click.echo(f"lgtmaybe {package_version()}")
    ctx.exit()


@click.group(epilog=_EPILOG)
@click.option(
    "--version",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_print_version,
    help="Show the lgtmaybe version and exit.",
)
@click.pass_context
def main(ctx: click.Context) -> None:
    """lgtmaybe — provider-agnostic PR reviewer."""
    _utf8_stdio()
    # Held open for the subcommand: click closes the context (and this resource)
    # once the command it dispatches to has returned.
    ctx.with_resource(graceful_interrupt())


@main.group(name="config")
def config_cmd() -> None:
    """Manage the user-level config (set provider/model/api_base once, reuse everywhere)."""


# Importing the commands module registers every command onto the groups above.
# Done last so the logic functions the commands call are already defined.
from lgtmaybe.cli import commands as _commands  # noqa: E402,F401

__all__ = [
    "RuntimeOptions",
    "action_inputs",
    "build_provider_engine",
    "build_review_context",
    "config_cmd",
    "execute_comment",
    "execute_local_review",
    "execute_review",
    "graceful_interrupt",
    "main",
    "pr_url_from_event",
    "render_findings",
    "resolve_auto_incremental",
    "run_describe",
    "run_review",
    "should_auto_describe",
]
