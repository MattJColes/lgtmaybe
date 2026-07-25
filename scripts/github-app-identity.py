#!/usr/bin/env python3
"""Exchange a GitHub Actions OIDC token for a scoped lgtmaybe App token."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

OIDC_AUDIENCE = "https://lgtmaybe.coles.codes/github-app-identity"
GITHUB_API_URL = "https://api.github.com"
TIMEOUT_SECONDS = 10.0


class IdentitySetupError(RuntimeError):
    """A user-actionable GitHub identity configuration error."""


Opener = Callable[[Request, float], Any]


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
    opener: Opener = urlopen,
    stdout: IO[str] = sys.stdout,
) -> None:
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
        with opener(oidc_request, TIMEOUT_SECONDS) as response:
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
        with opener(broker_request, TIMEOUT_SECONDS) as response:
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
    opener: Opener = urlopen,
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
        with opener(request, TIMEOUT_SECONDS):
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
