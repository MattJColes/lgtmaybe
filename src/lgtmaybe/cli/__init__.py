"""CLI for lgtmaybe.

This package is split into three layers:

- ``runtime`` / ``render`` — small, pure helpers (call-time options, output
  formatting).
- this module — the *logic*: parsing, adapter/provider wiring, and the
  ``execute_*`` entry points that the commands call. Kept together so the
  pipeline stages resolve (and can be patched in tests) as one namespace.
  It is the composition root: it builds the real adapters and hands them to
  the review use case in ``lgtmaybe.review``, which owns what a review run
  does and imports nothing from here.
- ``commands`` — the Click command + option declarations, imported at the
  bottom to register onto the groups defined here.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import click

from lgtmaybe.azure_devops import AzureDevOpsGateway
from lgtmaybe.cli.render import flatten_details, render_findings
from lgtmaybe.core.forge import GHES_UNSUPPORTED, Forge, PRLocator, token_env_vars
from lgtmaybe.core.forge import parse_pr_url as locate_pr
from lgtmaybe.core.logging import get_logger, register_secret
from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ReviewConfig,
    ReviewFinding,
    ReviewPreset,
    Severity,
)
from lgtmaybe.core.ports import (
    DiffUnavailable,
    ProviderClient,
    ReviewEngine,
    ReviewGateway,
    SupportsBaseCheckout,
    SupportsCompletionMark,
    SupportsFileContents,
    SupportsIncremental,
    SupportsPermissions,
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
from lgtmaybe.local import (
    LocalGitError,
    default_base,
    local_file_reader,
    local_pr_context,
    local_repo_root,
)
from lgtmaybe.providers.credentials import resolve_credentials
from lgtmaybe.providers.factory import (
    build_provider,
    cheaper_reflect_sibling,
    default_timeout_for,
    resolve_max_tokens,
)
from lgtmaybe.review import fail_on_check, fetch_context, marker_key, run_review


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

# Hidden marker on the notice posted when the host would not serve the diff
# (GitHub's 406 on a PR over 300 files / 20,000 lines). A posting gate can key
# on it the way it keys on the incomplete marker: this head was NOT reviewed,
# and the run said so rather than failing or passing.
SKIPPED_MARKER = "<!-- lgtmaybe-skipped -->"

# Exit status of a LOCAL `review --fail-on <severity>` when a finding is at or
# above the threshold — what lets a pre-commit / pre-push hook block on findings.
# Distinct from 1 (the review itself failed, a ClickException) and 2 (a click
# usage error), so a script can tell "found something" from "could not review".
FAIL_ON_EXIT_CODE = 3


#: Forge spellings of a pull-request event action that differ from GitHub's.
#: Gitea Actions writes a push to the PR into the event payload as its own
#: ``api.HookIssueSynchronized`` ("synchronized"); it converts that to
#: GitHub's name only when matching a workflow's ``types:``, never in the
#: payload itself.
_EVENT_ACTION_ALIASES = {"synchronized": "synchronize"}


def pr_event_action(event: dict[str, Any]) -> str:
    """The pull-request event's ``action``, in GitHub's spelling.

    Read once here so every comparison downstream (the overview gate, auto
    incremental) sees one name for a push, whichever forge sent it.
    """
    action = str(event.get("action") or "")
    return _EVENT_ACTION_ALIASES.get(action, action)


def base_changed(event: dict[str, Any]) -> bool:
    """Whether a pull-request event moved the PR onto another base branch.

    GitHub sends this as ``edited`` with ``changes.base``: a retarget, as when
    the PR below a stacked PR merges and the one above moves onto its base. The
    head is unchanged but the diff is not. Every other ``edited`` (title,
    description) changes no code.
    """
    changes = event.get("changes")
    return isinstance(changes, dict) and "base" in changes


def should_auto_diagram(cfg: ReviewConfig, *, event_action: str, retargeted: bool = False) -> bool:
    """Whether the action run should post or refresh the change overview.

    The overview's own sections (description, High Impact Areas) are config
    toggles inside it, not separate events: they ride this one comment, so a
    push refreshes all of them together or none of them. A retarget (see
    :func:`base_changed`) changes the diff the overview describes, so it
    refreshes too; any other ``edited`` does not.
    """
    return cfg.auto_diagram and (
        event_action in ("opened", "reopened", "synchronize") or retargeted
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


def resolve_ci_incremental(cfg: ReviewConfig) -> ReviewConfig:
    """Resolve ``incremental=None`` (auto) for a GitLab CI merge request pipeline.

    GitLab CI has no ``synchronize`` action to key on: every merge request
    pipeline looks alike. Auto therefore means "incremental whenever a completed
    watermark exists", which ``run_review`` already reduces to a full review on
    the first pipeline (no watermark), after a force-push or rebase, and on any
    compare failure. An explicit True/False still wins.
    """
    if cfg.incremental is not None:
        return cfg
    return cfg.model_copy(update={"incremental": True})


# Which forges lgtmaybe can build a gateway for. A forge that parses but is not
# in here is recognised-but-unimplemented, which earns a different (and much more
# useful) error than an unparseable URL.
_GATEWAY_BUILDERS: dict[Forge, Callable[[PRLocator, str, ReviewConfig], ReviewGateway]] = {
    Forge.github: lambda located, token, cfg: RestGitHubGateway(
        repo=located.repo,
        pr_number=located.number,
        token=token,
        marker_key=marker_key(cfg),
        resolve_fixed=cfg.resolve_fixed,
    ),
    Forge.gitlab: lambda located, token, cfg: GitLabGateway(
        host=located.host,
        repo=located.repo,
        pr_number=located.number,
        token=token,
        marker_key=marker_key(cfg),
        resolve_fixed=cfg.resolve_fixed,
        scheme=located.scheme,
    ),
    Forge.gitea: lambda located, token, cfg: GiteaGateway(
        host=located.host,
        repo=located.repo,
        pr_number=located.number,
        token=token,
        marker_key=marker_key(cfg),
        resolve_fixed=cfg.resolve_fixed,
        scheme=located.scheme,
    ),
    Forge.azure_devops: lambda located, token, cfg: AzureDevOpsGateway(
        host=located.host,
        repo=located.repo,
        pr_number=located.number,
        token=token,
        marker_key=marker_key(cfg),
        resolve_fixed=cfg.resolve_fixed,
        scheme=located.scheme,
        bearer=_azure_devops_token_is_bearer(),
    ),
}


def _forge_token(forge: Forge) -> tuple[str, str] | None:
    """``(variable, token)`` for the first of ``forge``'s token variables that is set.

    The one place token precedence is decided: ``build_review_context`` reads
    the token from it, and the Azure DevOps builder reads which variable won.
    """
    for var in token_env_vars(forge):
        if value := os.environ.get(var):
            return var, value
    return None


def _azure_devops_token_is_bearer() -> bool:
    """Whether the Azure DevOps token in play is the pipeline's own.

    A personal access token (``AZURE_DEVOPS_TOKEN``) is sent as basic auth; the
    job's OAuth token (``SYSTEM_ACCESSTOKEN``) as a bearer token. Which one is
    in play is whichever ``_forge_token`` picked, never re-derived here.
    """
    picked = _forge_token(Forge.azure_devops)
    return picked is not None and picked[0] == "SYSTEM_ACCESSTOKEN"


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
        azure_ad_token_provider=auth.azure_ad_token_provider,
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
            f"{located.forge} is not supported yet: lgtmaybe can only post reviews to "
            f"{', '.join(sorted(_GATEWAY_BUILDERS))} so far."
        )

    picked = _forge_token(located.forge)
    if picked is None:
        raise ValueError(
            f"{' or '.join(token_env_vars(located.forge))} is required to fetch the change "
            "and post the review. Set it in the environment (the GitHub Action provides it "
            "automatically)."
        )
    _token_var, token = picked
    register_secret(token)

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


def run_local_review(
    cfg: ReviewConfig,
    runtime: RuntimeOptions,
    *,
    base: str | None,
    working: bool,
    uncommitted: bool = False,
) -> tuple[list[ReviewFinding], str]:
    """Review the local git diff and RETURN the findings and summary.

    The one engine path behind every local review: the ``review`` command
    prints what this returns, and the MCP server hands it to a coding agent.
    Builds the provider straight from config/runtime (no token, no gateway) and
    resets the profiler, so a long-lived caller measures each review alone.
    A ``LocalGitError`` from building the context is converted here to a
    ``click.ClickException``, so both callers (the ``review`` command and the
    MCP tools) report the clean git message; every other failure propagates
    for the caller to surface.
    """
    profiler.reset()
    # Wire a read-only working-tree reader so reflection can resolve a deferred
    # verdict against the user's own checkout (safe — their branch, not PR code).
    fetch_file = local_file_reader()
    # And let ast-grep resolve a deferred symbol to its defining file by searching
    # that same worktree — the corpus is already on disk, so no clone is needed.
    resolve_symbol = build_symbol_resolver(local_repo_root) if cfg.symbol_resolution else None
    engine, _provider = build_provider_engine(
        cfg, runtime, fetch_file=fetch_file, resolve_symbol=resolve_symbol
    )
    try:
        ctx = local_pr_context(base=base, working=working, uncommitted=uncommitted)
    except LocalGitError as exc:
        # Converted at this seam so both callers of this function (the review
        # command and the MCP tools) report the clean message, not a prefix.
        raise click.ClickException(str(exc)) from exc
    # An empty diff has nothing to review: no model call, and never a 👍 LGTM.
    # Checked after the provider is built so a config error still surfaces first.
    if (nothing := _no_local_changes(ctx, base=base, uncommitted=uncommitted)) is not None:
        return [], nothing
    return engine.review(ctx, cfg)


def execute_local_review(
    cfg: ReviewConfig,
    runtime: RuntimeOptions,
    *,
    base: str | None,
    working: bool,
    uncommitted: bool = False,
    fmt: str,
    fail_on: Severity | None = None,
) -> None:
    """Review the local git diff and print findings — no GitHub involvement.

    Runs ``run_local_review`` and prints the result. Any failure surfaces as a
    clean CLI error — there is no PR to post a notice to.

    With *fail_on* (the explicit ``--fail-on`` flag, never the config file's
    ``fail_on``, which is the Action's merge gate) the run exits
    ``FAIL_ON_EXIT_CODE`` after printing when any finding is at or above it.
    """
    try:
        findings, summary = run_local_review(
            cfg, runtime, base=base, working=working, uncommitted=uncommitted
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    # json and sarif carry findings only, so the summary that says a lens failed
    # would otherwise be lost; stderr keeps stdout parseable.
    if fmt in ("json", "sarif") and INCOMPLETE_MARKER in summary:
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
    if fail_on is not None:
        conclusion, title, _check_summary = fail_on_check(findings, fail_on)
        if conclusion == "failure":
            # stderr: stdout is the deliverable (and may be --json).
            click.echo(f"lgtmaybe: {title} (--fail-on)", err=True)
            sys.exit(FAIL_ON_EXIT_CODE)


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
    """Print the change overview of local changes — no GitHub involvement.

    Builds the provider straight from config/runtime (no token, no gateway) and
    echoes the same Markdown body the ``/diagram`` comment would carry —
    description, High Impact Areas, and the Mermaid source (paste it into
    GitHub to render) plus the text rendering, which is what actually shows in
    a terminal — with the comment's collapsible wrappers flattened, since a
    terminal renders no HTML.
    """
    from lgtmaybe.engine.overview import build_overview

    ctx = _local_context(base=base, working=working, uncommitted=uncommitted)
    try:
        if (nothing := _no_local_changes(ctx, base=base, uncommitted=uncommitted)) is not None:
            click.echo(nothing)
            return
        _engine, provider = build_provider_engine(cfg, runtime)
        body = build_overview(ctx, cfg, provider)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(flatten_details(body))


def _local_context(*, base: str | None, working: bool, uncommitted: bool) -> PRContext:
    """The local diff's context, with git failures surfaced as a clean CLI error."""
    try:
        return local_pr_context(base=base, working=working, uncommitted=uncommitted)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc


def _no_local_changes(ctx: PRContext, *, base: str | None, uncommitted: bool) -> str | None:
    """What to say when the local diff is empty, or None when there is a diff.

    An empty comparison has nothing to review, so it costs no model call and is
    never reported as 👍 LGTM. When no base was given and none of the default
    branches exists, the base falls back to HEAD — a comparison with itself, so
    the emptiness says nothing about the change and the command fails, naming
    the flag that fixes it. A real base with no diff is a clean exit.
    """
    if ctx.diff.strip():
        return None
    if uncommitted:
        return "no uncommitted changes vs HEAD: nothing to review"
    resolved = base or default_base(None)
    if base is None and resolved == "HEAD":
        raise click.ClickException(
            "no changes vs HEAD: found no origin/HEAD, origin/main, origin/master, "
            "main or master to compare against; pass --base <branch>"
        )
    return f"no changes vs {resolved}: nothing to review; pass --base <branch> to compare another"


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


def execute_review(
    cfg: ReviewConfig,
    runtime: RuntimeOptions,
    *,
    diagram: bool = False,
) -> None:
    """Build adapters, run the review, and surface failures back to the PR.

    Shared by the ``review`` command and Action entrypoint. An automatic change
    overview is a required completion step inside ``run_review``, and reuses
    this run's adapter set and prefetched O(files) PR context.
    """
    profiler.reset()
    github, engine, provider = _build_context_or_fail(cfg, runtime)

    ctx: PRContext | None = None
    # From here we have a gateway, so any failure is surfaced back to the PR as
    # a short comment rather than failing silently — then we exit non-zero.
    try:
        if diagram:
            # A failed prefetch means run_review fetches and surfaces the
            # failure itself. Fetching here lets the review and the change
            # overview share one current-head context. A refused diff is
            # not retried by run_review — it is the same answer twice.
            try:
                ctx = fetch_context(github, cfg)
            except DiffUnavailable:
                raise
            except Exception:
                _log.warning("PR context prefetch failed — overview skipped", exc_info=True)
        run_review(
            github=github,
            engine=engine,
            cfg=cfg,
            dry_run=False,
            ctx=ctx,
            provider=provider,
            diagram_required=diagram,
        )
    except DiffUnavailable as exc:
        # The host will not serve the diff (GitHub: over 300 files / 20,000
        # lines). No diff means no content was seen, and no retry changes
        # that — so this is disclosed as a review that did not happen, not
        # reported as one that failed. Exit zero: a red job for a PR the
        # reviewer was never able to look at only teaches people to ignore
        # red jobs.
        _log.warning("diff unavailable — review skipped", extra={"reason": exc.reason})
        _post_skipped(github, exc)
        click.echo(f"review skipped: {exc.reason}")
        return
    except Exception as exc:
        _post_failure(github, exc)
        raise click.ClickException(f"review failed: {exc}") from exc

    if runtime.profile:
        click.echo(profiler.render())


def execute_comment(event: dict[str, Any], cfg: ReviewConfig, runtime: RuntimeOptions) -> None:
    """Route an issue_comment event's slash command to the engine/provider.

    Shared by the ``comment`` command and the ``action`` entrypoint. ``runtime``
    supplies api_key/api_base/fallback_model; the PR URL is derived here.
    """
    from lgtmaybe.cli.slash import dispatch, parse_command

    # An edited or deleted comment re-delivers its original body and author, so
    # a workflow without a `types: [created]` filter would re-run a paid review
    # on a maintainer tidying an old command.
    action = event.get("action", "created")
    if action != "created":
        click.echo(f"Comment event is '{action}', not 'created'; ignoring.")
        return

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

    # Anyone can comment on a public PR, and a command spends the model, so it
    # runs only for someone who can push. Checked here, not just in the example
    # workflows' `if:` — a workflow without that gate (Gitea's cannot read
    # author_association) must not hand the reviewer to a stranger. Fails closed:
    # no login, or an adapter that cannot tell, is not authorised.
    author = (event.get("comment", {}).get("user") or {}).get("login") or ""
    if not (author and isinstance(github, SupportsPermissions) and github.has_repo_write(author)):
        _log.warning("slash command ignored: no write access", extra={"author": author})
        click.echo(f"/{parsed.name} ignored: {author or 'the commenter'} has no write access.")
        return

    try:
        dispatch(parsed, github=github, engine=engine, provider=provider, cfg=cfg)
    except DiffUnavailable as exc:
        # Same disclosure as execute_review: no diff, nothing seen, exit zero.
        _log.warning("diff unavailable — command skipped", extra={"reason": exc.reason})
        _post_skipped(github, exc)
        click.echo(f"/{parsed.name} skipped: {exc.reason}")
        return
    except Exception as exc:
        _post_failure(github, exc)
        raise click.ClickException(f"/{parsed.name} failed: {exc}") from exc


def _post_skipped(github: ReviewGateway, exc: DiffUnavailable) -> None:
    """Post the not-reviewed notice for a diff the host refused to serve.

    Says three things, in this order: nothing was looked at, why (in the
    host's words), and what moves it — a smaller PR, since the limit is the
    host's and no reviewer setting changes it. Same two writes as a failure
    notice, for the same reason: on a re-run the review-body edit notifies
    nobody.
    """
    notice = (
        "ℹ️ lgtmaybe did not review this pull request: GitHub would not serve "
        f"its diff, so **none of its content was seen**.\n\n> {exc.reason}\n\n"
        "GitHub stops rendering a diff past 300 files or 20,000 lines. lgtmaybe "
        "reads pull request content through that API only (never a checkout), "
        "so there is nothing to review until the change is split into smaller "
        "pull requests. This is not a review and not an approval: **not reviewed**."
        f"\n\n{SKIPPED_MARKER}\n_lgtmaybe {package_version()}_"
    )
    _post_notice(github, notice)


def _post_failure(github: ReviewGateway, exc: Exception) -> None:
    """Post a short failure notice to the PR; never raise from here."""
    # Name the version: unlike the summary line this notice carries no model,
    # so without it a failure report says nothing about what was running.
    _post_notice(github, f"⚠️ lgtmaybe review failed: {exc}\n\n_lgtmaybe {package_version()}_")


def _post_notice(github: ReviewGateway, notice: str) -> None:
    """Post a findings-free notice as the review body AND a PR comment; never raise.

    The body write is skipped when it would overwrite a completed (watermarked)
    summary; the comment alone then carries the notice.
    """
    try:
        # run_review may have prepared the reviewed watermark before the post
        # failed — clear it so the failure notice doesn't carry it and the next
        # incremental run re-reviews the commits whose findings never posted.
        if isinstance(github, SupportsCompletionMark):
            github.mark_reviewed(None)
        # A summary carrying a watermark is the record of the last head that WAS
        # reviewed — its demoted findings and the watermark itself. Writing the
        # notice over it erased both; the PR comment below discloses the
        # failure instead, and the watermark stays where that success left it.
        if not isinstance(github, SupportsIncremental) or github.last_reviewed_sha() is None:
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

    Forgejo (a Gitea fork, same API and URL shape) sets ``FORGEJO_SERVER_URL``
    beside the ``GITHUB_*`` aliases from forgejo-runner v7. It is read first, so
    a Forgejo run never rests on the alias and is never taken for GHES.
    """
    forgejo = os.environ.get("FORGEJO_SERVER_URL", "").rstrip("/")
    if forgejo:
        return f"{forgejo}/{repo}/pulls/{number}"
    server = os.environ.get("GITHUB_SERVER_URL", "").rstrip("/")
    if not server or server == "https://github.com":
        return f"https://github.com/{repo}/pull/{number}"
    if os.environ.get("GITHUB_API_URL", "").rstrip("/").endswith("/api/v3"):
        raise click.ClickException(GHES_UNSUPPORTED)
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

    A server URL with a path is refused: the URL alone cannot tell a sub-path
    from a nested group, so this is the one place the difference is knowable.
    """
    host = os.environ.get("CI_SERVER_HOST") or ""
    server = os.environ.get("CI_SERVER_URL", "").rstrip("/") or (host and f"https://{host}")
    if not server:
        raise click.ClickException(
            "CI_SERVER_HOST is not set, so lgtmaybe cannot tell which GitLab to post to."
        )
    subpath = urlsplit(server).path.strip("/")
    if subpath:
        raise click.ClickException(
            f"CI_SERVER_URL is {server}: a GitLab served under the sub-path /{subpath} is "
            "not supported. The sub-path would parse into the project path and "
            "GITLAB_TOKEN would be sent to the wrong endpoint. Serve GitLab at the host "
            "root, e.g. https://gitlab.example.com/group/proj/-/merge_requests/7."
        )
    project = os.environ.get("CI_MERGE_REQUEST_PROJECT_PATH") or os.environ.get("CI_PROJECT_PATH")
    if not project:
        raise click.ClickException("CI_PROJECT_PATH is not set: no project to review.")
    iid = os.environ.get("CI_MERGE_REQUEST_IID")
    if not iid:
        raise click.ClickException(
            "CI_MERGE_REQUEST_IID is not set: this pipeline is not for a merge request. "
            'Run the job with `rules: - if: $CI_PIPELINE_SOURCE == "merge_request_event"`.'
        )
    return f"{server}/{project}/-/merge_requests/{iid}"


def pr_url_from_pipelines_env() -> str:
    """Build the pull request URL from Azure Pipelines' predefined variables.

    The Azure DevOps counterpart to ``mr_url_from_ci_env``: no event payload,
    so the collection, project, repository, and pull request id come from the
    job's environment and are assembled into the URL the shared locator parses.

    A missing pull request id usually means the pipeline ran as a CI build
    rather than as the build validation policy on a branch, so the message
    names the variable and the fix.
    """
    # Azure Pipelines always sets this ("TfsGit" for Azure Repos). An unset or
    # empty value only happens outside a pipeline (running the command by hand
    # with the other variables exported), and is treated as Azure Repos: the
    # URL it builds still has to parse as an Azure DevOps pull request.
    provider = os.environ.get("BUILD_REPOSITORY_PROVIDER", "")
    if provider and provider != "TfsGit":
        raise click.ClickException(
            f"This pipeline builds a {provider} repository; `azure-pipelines` reviews Azure "
            "Repos pull requests only. For a GitHub repository, use the GitHub Action."
        )
    required = {
        name: os.environ.get(name, "").strip()
        for name in ("SYSTEM_COLLECTIONURI", "SYSTEM_TEAMPROJECT", "BUILD_REPOSITORY_NAME")
    }
    for name, value in required.items():
        if not value:
            raise click.ClickException(
                f"{name} is not set; run `lgtmaybe azure-pipelines` inside an Azure Pipelines job."
            )
    pr_id = os.environ.get("SYSTEM_PULLREQUEST_PULLREQUESTID", "").strip()
    if not pr_id.isdigit():
        raise click.ClickException(
            "SYSTEM_PULLREQUEST_PULLREQUESTID is not set: this build is not validating a pull "
            "request. Run the pipeline from a build validation branch policy."
        )
    collection = required["SYSTEM_COLLECTIONURI"].rstrip("/")
    project = quote(required["SYSTEM_TEAMPROJECT"], safe="")
    repo = quote(required["BUILD_REPOSITORY_NAME"], safe="")
    return f"{collection}/{project}/_git/{repo}/pullrequest/{pr_id}"


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
#: static-analysis and risk toggles, and the config file path.
_RUNTIME_INPUTS = frozenset(
    {"api_key", "api_base", "fallback_model", "profile", "static_analysis", "risk", "config_path"}
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
        "project",
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
  lgtmaybe mcp                             Serve reviews to a coding agent over MCP
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
    "FAIL_ON_EXIT_CODE",
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
    "pr_event_action",
    "pr_url_from_event",
    "render_findings",
    "resolve_auto_incremental",
    "resolve_ci_incremental",
    "run_local_review",
]
