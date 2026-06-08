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
import re
from typing import Any

import click

from lgtmaybe.cli.render import render_findings
from lgtmaybe.cli.runtime import RuntimeOptions
from lgtmaybe.core.models import Provider, ReviewConfig, ReviewFinding
from lgtmaybe.core.ports import GitHubGateway, ProviderClient, ReviewEngine
from lgtmaybe.engine import LLMReviewEngine
from lgtmaybe.github import RestGitHubGateway
from lgtmaybe.local import local_pr_context
from lgtmaybe.providers.credentials import resolve_credentials
from lgtmaybe.providers.factory import build_provider

_PR_URL_RE = re.compile(r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)")


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
    cfg: ReviewConfig, runtime: RuntimeOptions
) -> tuple[LLMReviewEngine, ProviderClient]:
    """Resolve credentials and build the provider + engine from config + runtime.

    Shared by every path that needs to talk to the model — the GitHub gateway
    wiring and the local review alike — so credential resolution and provider
    options stay in exactly one place. Raises ValueError with an actionable
    message when a required credential is missing.
    """
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
    provider = build_provider(
        cfg.provider,
        cfg.model,
        api_key=auth.api_key,
        api_base=auth.api_base,
        azure_ad_token=auth.azure_ad_token,
        fallback_model=runtime.fallback_model,
        timeout=cfg.timeout,
        temperature=cfg.temperature,
        **extra,
    )
    return LLMReviewEngine(provider), provider


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

    engine, provider = build_provider_engine(cfg, runtime)
    github = RestGitHubGateway(
        repo=repo,
        pr_number=pr_number,
        token=token,
        marker_key=f"{cfg.provider}/{cfg.model}",
    )
    return github, engine, provider


def build_adapters(
    cfg: ReviewConfig, runtime: RuntimeOptions
) -> tuple[GitHubGateway, ReviewEngine]:
    """Construct the real GitHub gateway and review engine from config + runtime."""
    github, engine, _provider = build_review_context(cfg, runtime)
    return github, engine


def run_review(
    *,
    github: GitHubGateway,
    engine: ReviewEngine,
    cfg: ReviewConfig,
    dry_run: bool,
) -> tuple[list[ReviewFinding], str]:
    """Core review pipeline — pure function over injected ports.

    Fetches PR context, runs the engine, and optionally posts the review.
    Returns (findings, summary) in all cases so callers can inspect output.
    """
    ctx = github.get_pr_context()
    findings, summary = engine.review(ctx, cfg)

    if not dry_run:
        github.post_review(findings, summary)

    return findings, summary


def execute_local_review(
    cfg: ReviewConfig,
    runtime: RuntimeOptions,
    *,
    base: str | None,
    working: bool,
    fmt: str,
) -> None:
    """Review the local git diff and print findings — no GitHub involvement.

    Builds the provider straight from config/runtime (no token, no gateway),
    runs the engine over the local diff, and prints the result. Any failure
    surfaces as a clean CLI error — there is no PR to post a notice to.
    """
    try:
        engine, _provider = build_provider_engine(cfg, runtime)
        ctx = local_pr_context(base=base, working=working)
        findings, summary = engine.review(ctx, cfg)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(render_findings(findings, summary, fmt=fmt))


def execute_review(cfg: ReviewConfig, runtime: RuntimeOptions, *, dry_run: bool) -> None:
    """Build adapters, run the review, surface failures back to the PR.

    Shared by the ``review`` command and the ``action`` entrypoint.
    """
    # Adapter construction can fail before we have any way to post (bad URL,
    # missing token/credentials). Surface those as a clean CLI error.
    try:
        github, engine = build_adapters(cfg, runtime)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    # From here we have a gateway, so any failure is surfaced back to the PR as
    # a short comment rather than failing silently — then we exit non-zero.
    try:
        findings, summary = run_review(github=github, engine=engine, cfg=cfg, dry_run=dry_run)
    except Exception as exc:
        if not dry_run:
            _post_failure(github, exc)
        raise click.ClickException(f"review failed: {exc}") from exc

    if dry_run:
        click.echo(f"[dry-run] {summary}")
        click.echo(json.dumps([f.model_dump(mode="json") for f in findings]))


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

    repo = event["repository"]["full_name"]
    pr_number = issue["number"]
    runtime = runtime.with_pr_url(f"https://github.com/{repo}/pull/{pr_number}")

    try:
        github, engine, provider_client = build_review_context(cfg, runtime)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        dispatch(parsed, github=github, engine=engine, provider=provider_client, cfg=cfg)
    except Exception as exc:
        _post_failure(github, exc)
        raise click.ClickException(f"/{parsed.name} failed: {exc}") from exc


def _post_failure(github: GitHubGateway, exc: Exception) -> None:
    """Post a short failure notice to the PR; never raise from here."""
    notice = f"⚠️ lgtmaybe review failed: {exc}"
    try:
        github.post_review([], notice)
    except Exception:
        # Posting the failure notice itself failed — nothing more we can do;
        # the original error is still surfaced by the caller's ClickException.
        pass


def pr_url_from_event(event: dict[str, Any]) -> str:
    """Build the PR URL from a pull_request(_target) event payload.

    Uses ``GITHUB_SERVER_URL`` so it works on GitHub Enterprise too.
    """
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repo = event["repository"]["full_name"]
    number = event["pull_request"]["number"]
    return f"{server}/{repo}/pull/{number}"


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
        "fallback_model": get("FALLBACK_MODEL"),
        "api_key": get("API_KEY"),
        "api_base": get("API_BASE"),
        "timeout": get("TIMEOUT"),
        "temperature": get("TEMPERATURE"),
        "num_ctx": get("NUM_CTX"),
        "max_input_tokens": get("MAX_INPUT_TOKENS"),
        "config_path": os.environ.get("INPUT_CONFIG_PATH") or ".lgtmaybe.yml",
    }


@click.group()
def main() -> None:
    """lgtmaybe — provider-agnostic PR reviewer."""


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
    "execute_local_review",
    "execute_review",
    "main",
    "parse_pr_url",
    "pr_url_from_event",
    "render_findings",
    "run_review",
]
