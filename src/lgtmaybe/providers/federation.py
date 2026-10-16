"""Keyless cloud credentials from a CI run's own OIDC ID token.

On GitHub, the composite action exchanges the workflow's OIDC token for cloud
credentials in its own steps, before the container starts. A runner that runs
the lgtmaybe image directly (Forgejo Actions) has no such steps, so the
``action`` entrypoint does the exchange here instead.

Nothing new is invented on the cloud side. The ID token is written to a private
file and exposed only through the variables each SDK already reads natively:
botocore's web-identity provider, azure-identity's workload identity
credential, and a google-auth ``external_account`` config. Credential
resolution (``providers/credentials.py``) is unchanged: it finds these as
ambient credentials.

Federation runs only when a cloud's input asks for it. With none set it makes
no request and returns no variables, so every other run is unchanged. The
composite action never passes these inputs into its container, so a GitHub run
is never federated twice.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

_TIMEOUT = 30.0

_AWS_AUDIENCE = "sts.amazonaws.com"
_AZURE_AUDIENCE = "api://AzureADTokenExchange"
_GCP_STS_URL = "https://sts.googleapis.com/v1/token"
_GCP_IMPERSONATION_URL = (
    "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{}:generateAccessToken"
)

_MISSING_TOKEN = (
    "{input} is set, but this job has no OIDC ID token to federate with "
    "(ACTIONS_ID_TOKEN_REQUEST_URL / ACTIONS_ID_TOKEN_REQUEST_TOKEN are not set). "
    "On Forgejo, add `enable-openid-connect: true` to the job; on GitHub, grant "
    "`permissions: id-token: write`."
)


class FederationError(RuntimeError):
    """A requested federation cannot proceed. The message says how to fix it."""


@dataclass(frozen=True, slots=True)
class _Request:
    """One cloud's federation: the input that asked for it and the token audience."""

    trigger: str
    audience: str


def _input(env: Mapping[str, str], name: str) -> str | None:
    """An ``INPUT_*`` value; an unset optional workflow input arrives as blank."""
    value = env.get(name, "").strip()
    return value or None


def _requests(env: Mapping[str, str]) -> list[_Request]:
    """Every federation the inputs ask for, checked as a set before any request."""
    requests: list[_Request] = []
    if _input(env, "INPUT_AWS_ROLE_ARN"):
        region = (
            _input(env, "INPUT_AWS_REGION")
            or env.get("AWS_REGION")
            or env.get("AWS_DEFAULT_REGION")
        )
        if not region:
            raise FederationError(
                "INPUT_AWS_ROLE_ARN is set but no region is: set INPUT_AWS_REGION "
                "(or AWS_REGION, or AWS_DEFAULT_REGION) to the Bedrock region."
            )
        requests.append(_Request("INPUT_AWS_ROLE_ARN", _AWS_AUDIENCE))
    if _input(env, "INPUT_AZURE_CLIENT_ID"):
        if not _input(env, "INPUT_AZURE_TENANT_ID"):
            raise FederationError(
                "INPUT_AZURE_CLIENT_ID is set but INPUT_AZURE_TENANT_ID is not: Entra "
                "needs both to trust the workflow's token."
            )
        requests.append(_Request("INPUT_AZURE_CLIENT_ID", _AZURE_AUDIENCE))
    provider = _input(env, "INPUT_GCP_WIF_PROVIDER")
    if provider:
        requests.append(
            _Request("INPUT_GCP_WIF_PROVIDER", f"https://iam.googleapis.com/{provider}")
        )
    return requests


def _fetch_id_token(env: Mapping[str, str], audience: str, client: httpx.Client) -> str:
    """Ask the runner for an ID token minted for ``audience``.

    The request URL carries the runner's own query (``api-version``), so the
    audience is merged in rather than appended. Errors name the HTTP status
    only: the URL and both tokens stay out of every message.
    """
    url = httpx.URL(env["ACTIONS_ID_TOKEN_REQUEST_URL"]).copy_merge_params({"audience": audience})
    headers = {"Authorization": f"Bearer {env['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"}
    try:
        response = client.get(url, headers=headers, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise FederationError(
            f"requesting the workflow's OIDC ID token failed ({type(exc).__name__})"
        ) from None
    if response.status_code != 200:
        raise FederationError(
            f"requesting the workflow's OIDC ID token failed: HTTP {response.status_code}"
        )
    try:
        token = response.json().get("value")
    except (ValueError, AttributeError):
        token = None
    if not isinstance(token, str) or not token:
        raise FederationError("the runner answered the OIDC ID token request with no token")
    return token


def _write_private(directory: Path, name: str, text: str) -> Path:
    """Write ``text`` to a new owner-only file; refuse to reuse an existing one."""
    path = directory / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def federate_ci_credentials(
    env: Mapping[str, str],
    *,
    client: httpx.Client | None = None,
    workdir: Path | None = None,
) -> dict[str, str]:
    """Federate every cloud the inputs ask for; return the variables to set.

    Returns an empty mapping, having made no request, when no federation input
    is set. Raises ``FederationError`` (naming the fix) when an input set is
    incomplete, the job has no ID token, or the runner refuses to mint one.
    The caller applies the returned variables to the process environment.
    """
    requests = _requests(env)
    if not requests:
        return {}
    for request in requests:
        if not (
            env.get("ACTIONS_ID_TOKEN_REQUEST_URL") and env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
        ):
            raise FederationError(_MISSING_TOKEN.format(input=request.trigger))

    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix="lgtmaybe-oidc-"))
        # The SDKs re-read these files whenever they refresh, so they must
        # outlive this call; they are removed at exit instead. A container is
        # thrown away anyway, but a pip-installed host runner keeps /tmp.
        atexit.register(shutil.rmtree, workdir, ignore_errors=True)
    if client is not None:
        return _federate(env, requests, client, workdir)
    with httpx.Client() as http:
        return _federate(env, requests, http, workdir)


def _federate(
    env: Mapping[str, str], requests: list[_Request], http: httpx.Client, directory: Path
) -> dict[str, str]:
    """Fetch one token per request and return the SDK variables for each cloud."""
    updates: dict[str, str] = {}
    for request in requests:
        token = _fetch_id_token(env, request.audience, http)
        if request.trigger == "INPUT_AWS_ROLE_ARN":
            path = _write_private(directory, "aws-web-identity-token", token)
            updates |= {
                "AWS_ROLE_ARN": _input(env, "INPUT_AWS_ROLE_ARN") or "",
                "AWS_WEB_IDENTITY_TOKEN_FILE": str(path),
                "AWS_ROLE_SESSION_NAME": "lgtmaybe",
            }
            region = _input(env, "INPUT_AWS_REGION")
            if region:
                updates["AWS_REGION"] = region
        elif request.trigger == "INPUT_AZURE_CLIENT_ID":
            path = _write_private(directory, "azure-federated-token", token)
            updates |= {
                "AZURE_FEDERATED_TOKEN_FILE": str(path),
                "AZURE_CLIENT_ID": _input(env, "INPUT_AZURE_CLIENT_ID") or "",
                "AZURE_TENANT_ID": _input(env, "INPUT_AZURE_TENANT_ID") or "",
            }
        else:
            path = _write_private(directory, "gcp-subject-token", token)
            provider = _input(env, "INPUT_GCP_WIF_PROVIDER")
            config: dict[str, object] = {
                "type": "external_account",
                "audience": f"//iam.googleapis.com/{provider}",
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "token_url": _GCP_STS_URL,
                "credential_source": {"file": str(path)},
            }
            account = _input(env, "INPUT_GCP_SERVICE_ACCOUNT")
            if account:
                config["service_account_impersonation_url"] = _GCP_IMPERSONATION_URL.format(account)
            config_path = _write_private(directory, "gcp-external-account.json", json.dumps(config))
            updates["GOOGLE_APPLICATION_CREDENTIALS"] = str(config_path)
    return updates
