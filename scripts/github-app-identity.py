#!/usr/bin/env python3
"""Exchange a GitHub Actions OIDC token for a scoped lgtmaybe App token."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

OIDC_AUDIENCE = "https://lgtmaybe.coles.codes/github-app-identity"
GITHUB_API_URL = "https://api.github.com"
TIMEOUT_SECONDS = 10.0

# Events whose workflow run does NOT come from the repository's default branch,
# so the broker's default-branch assertion can never be satisfied.
#
# That assertion is not an obstacle to work around — it is what stops a
# contributor from editing `.github/workflows/` on their own PR branch and
# minting an lgtmaybe App token from it. The right response is therefore not to
# ask: attempting the exchange only turns a reply into a failed review job.
#
# `issue_comment` and `pull_request_target` are deliberately absent. Both DO run
# from the default branch and mint branded identity normally, so widening this
# set would downgrade identity for the review path that works today.
_NON_DEFAULT_BRANCH_EVENTS = frozenset({"pull_request_review_comment"})


class IdentitySetupError(RuntimeError):
    """A user-actionable GitHub identity configuration error."""


class Opener(Protocol):
    def __call__(self, request: Request, *, timeout: float) -> Any: ...


def _open_url(request: Request, *, timeout: float) -> Any:
    return urlopen(request, timeout=timeout)


def validate(env: Mapping[str, str]) -> None:
    identity = env.get("GITHUB_IDENTITY", "actions")
    if identity not in {"actions", "lgtmaybe"}:
        raise IdentitySetupError("github_identity must be either 'actions' or 'lgtmaybe'.")

    app_id = env.get("APP_ID", "")
    private_key = env.get("APP_PRIVATE_KEY", "")
    if identity == "lgtmaybe" and (app_id or private_key):
        raise IdentitySetupError(
            "Choose either github_identity: lgtmaybe or app_id/app_private_key, not both."
        )
    if bool(app_id) != bool(private_key):
        raise IdentitySetupError("app_id and app_private_key must be provided together.")
    if identity == "lgtmaybe" and env.get("FAIL_ON"):
        raise IdentitySetupError(
            "github_identity: lgtmaybe cannot be combined with fail_on because the "
            "public App intentionally has no Checks: write permission. Use "
            "github_identity: actions or a self-managed App with Checks: write."
        )


def exchange(
    env: Mapping[str, str],
    *,
    opener: Opener = _open_url,
    stdout: IO[str] = sys.stdout,
) -> None:
    if env.get("GITHUB_EVENT_NAME", "") in _NON_DEFAULT_BRANCH_EVENTS:
        # Write no token: action.yml falls back to the workflow token via
        # `steps.lgtmaybe-token.outputs.token || ... || inputs.github_token`,
        # and the revoke step stays skipped because it guards on that output.
        stdout.write(
            "::notice::lgtmaybe posts as github-actions[bot] on this event. "
            "Branded App identity needs a workflow run from the default branch, "
            "which this event does not provide.\n"
        )
        return

    oidc_url = env.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    oidc_request_token = env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not oidc_url or not oidc_request_token:
        raise IdentitySetupError(
            "The lgtmaybe bot identity needs `permissions: id-token: write` in the workflow."
        )

    broker_url = env.get("IDENTITY_BROKER_URL")
    output_path = env.get("GITHUB_OUTPUT")
    if not broker_url or not output_path:
        raise IdentitySetupError("The lgtmaybe identity exchange is not configured.")

    try:
        oidc_request = Request(
            _with_audience(oidc_url),
            headers={"Authorization": f"Bearer {oidc_request_token}"},
        )
        with opener(oidc_request, timeout=TIMEOUT_SECONDS) as response:
            oidc_token = _json(response).get("value")
        if not isinstance(oidc_token, str) or not oidc_token:
            raise IdentitySetupError("GitHub did not return a workflow identity token.")

        broker_request = Request(
            broker_url,
            data=b"",
            method="POST",
            headers={
                "Authorization": f"Bearer {oidc_token}",
                "Accept": "application/json",
            },
        )
        with opener(broker_request, timeout=TIMEOUT_SECONDS) as response:
            app_token = _json(response).get("token")
        if not isinstance(app_token, str) or not app_token:
            raise IdentitySetupError("The identity broker did not return an App token.")
    except IdentitySetupError:
        raise
    except HTTPError as error:
        raise IdentitySetupError(_http_error_message(error)) from error
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        raise IdentitySetupError(
            "The lgtmaybe identity broker is unavailable. Retry the workflow shortly."
        ) from error

    stdout.write(f"::add-mask::{app_token}\n")
    with Path(output_path).open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"token={app_token}\n")


def revoke(
    env: Mapping[str, str],
    *,
    opener: Opener = _open_url,
    stdout: IO[str] = sys.stdout,
) -> None:
    token = env.get("LGTMAYBE_TOKEN")
    if not token:
        return

    request = Request(
        f"{GITHUB_API_URL}/installation/token",
        method="DELETE",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with opener(request, timeout=TIMEOUT_SECONDS):
            pass
    except (HTTPError, URLError, OSError):
        stdout.write(
            "::warning::Could not revoke the temporary lgtmaybe App token; "
            "GitHub will expire it automatically.\n"
        )


def _with_audience(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.append(("audience", OIDC_AUDIENCE))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _json(response: Any) -> dict[str, object]:
    payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    return payload


def _http_error_message(error: HTTPError) -> str:
    try:
        payload = json.loads(error.read())
        message = payload.get("message") if isinstance(payload, dict) else None
        if isinstance(message, str) and message:
            return message
    except (OSError, ValueError, json.JSONDecodeError):
        pass

    if error.code == 401:
        return "GitHub rejected the workflow identity. Check `permissions: id-token: write`."
    return "The lgtmaybe identity broker rejected this workflow."


def main(argv: list[str] | None = None, env: Mapping[str, str] = os.environ) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1 or args[0] not in {"validate", "exchange", "revoke"}:
        print("usage: github-app-identity.py {validate|exchange|revoke}", file=sys.stderr)
        return 2

    try:
        if args[0] == "validate":
            validate(env)
        elif args[0] == "exchange":
            exchange(env)
        else:
            revoke(env)
    except IdentitySetupError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
