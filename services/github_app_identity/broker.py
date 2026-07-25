from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
import jwt
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

OIDC_ISSUER = "https://token.actions.githubusercontent.com"
OIDC_AUDIENCE = "https://lgtmaybe.coles.codes/github-app-identity"
OIDC_JWKS_URL = f"{OIDC_ISSUER}/.well-known/jwks"
INSTALL_URL = "https://github.com/apps/lgtmaybe/installations/new"
GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
ALLOWED_EVENTS = frozenset({"pull_request_target", "issue_comment", "pull_request_review_comment"})
TOKEN_PERMISSIONS = {
    "contents": "read",
    "pull_requests": "write",
    "issues": "write",
}
HTTP_TIMEOUT = httpx.Timeout(5.0)

logger = logging.getLogger(__name__)


class BrokerError(ValueError):
    def __init__(self, code: str, status_code: int, message: str):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class InstallationToken:
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class GitHubRepository:
    id: int
    full_name: str
    owner_id: int
    default_branch: str


@dataclass(frozen=True, slots=True)
class OidcClaims:
    repository: str
    repository_id: int
    repository_owner_id: int
    event_name: str
    workflow_ref: str
    jti: str

    @classmethod
    def parse(cls, raw: Mapping[str, object]) -> OidcClaims:
        try:
            repository = _required_string(raw, "repository")
            return cls(
                repository=repository,
                repository_id=_required_integer(raw, "repository_id"),
                repository_owner_id=_required_integer(raw, "repository_owner_id"),
                event_name=_required_string(raw, "event_name"),
                workflow_ref=_required_string(raw, "workflow_ref"),
                jti=_required_string(raw, "jti"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BrokerError(
                "invalid_oidc",
                401,
                "GitHub OIDC token is missing required repository or workflow claims.",
            ) from exc


class OidcVerifier(Protocol):
    def verify(self, token: str) -> Mapping[str, object]: ...


class GitHubClient(Protocol):
    def find_installation(self, full_name: str) -> int: ...

    def create_token(
        self,
        installation_id: int,
        repository_id: int,
        permissions: dict[str, str],
    ) -> InstallationToken: ...

    def get_repository(self, full_name: str, token: str) -> GitHubRepository: ...

    def revoke_token(self, token: str) -> None: ...


class JwksClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


class PyJwtOidcVerifier:
    def __init__(self, jwks_client: JwksClient | None = None):
        self._jwks_client = jwks_client or jwt.PyJWKClient(OIDC_JWKS_URL, timeout=5.0)

    def verify(self, token: str) -> Mapping[str, object]:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=OIDC_AUDIENCE,
                issuer=OIDC_ISSUER,
                options={
                    "require": [
                        "exp",
                        "iat",
                        "nbf",
                        "jti",
                        "repository",
                        "repository_id",
                        "repository_owner_id",
                        "event_name",
                        "workflow_ref",
                    ]
                },
            )
        except (jwt.PyJWKClientError, jwt.InvalidTokenError) as exc:
            raise BrokerError(
                "invalid_oidc",
                401,
                "GitHub OIDC token was rejected. Check `id-token: write` and workflow origin.",
            ) from exc
        if not isinstance(claims, dict):
            raise BrokerError("invalid_oidc", 401, "GitHub OIDC token payload is invalid.")
        return claims


class IdentityBroker:
    def __init__(self, verifier: OidcVerifier, github: GitHubClient):
        self._verifier = verifier
        self._github = github

    def exchange(self, oidc_token: str) -> InstallationToken:
        claims = OidcClaims.parse(self._verifier.verify(oidc_token))
        self._validate_pre_mint(claims)

        installation_id = self._github.find_installation(claims.repository)
        token = self._github.create_token(
            installation_id,
            claims.repository_id,
            TOKEN_PERMISSIONS.copy(),
        )
        try:
            repository = self._github.get_repository(claims.repository, token.token)
            self._validate_repository(claims, repository)
        except Exception:
            self._revoke_after_rejection(token.token)
            raise

        logger.info(
            "GitHub App identity exchanged repository_id=%s event=%s",
            repository.id,
            claims.event_name,
        )
        return token

    @staticmethod
    def _validate_pre_mint(claims: OidcClaims) -> None:
        workflow_prefix = f"{claims.repository}/.github/workflows/"
        if claims.event_name not in ALLOWED_EVENTS or not claims.workflow_ref.startswith(
            workflow_prefix
        ):
            raise BrokerError(
                "untrusted_workflow",
                403,
                "Branded identity is limited to a default-branch lgtmaybe workflow "
                "triggered by pull_request_target or a PR comment.",
            )

    @staticmethod
    def _validate_repository(claims: OidcClaims, repository: GitHubRepository) -> None:
        expected_ref = f"@refs/heads/{repository.default_branch}"
        if (
            repository.id != claims.repository_id
            or repository.owner_id != claims.repository_owner_id
            or repository.full_name.casefold() != claims.repository.casefold()
        ):
            raise BrokerError(
                "repository_mismatch",
                403,
                "GitHub repository identity no longer matches the signed workflow identity.",
            )
        if not claims.workflow_ref.endswith(expected_ref):
            raise BrokerError(
                "untrusted_workflow",
                403,
                "The lgtmaybe identity exchange must run from the repository's default branch.",
            )

    def _revoke_after_rejection(self, token: str) -> None:
        try:
            self._github.revoke_token(token)
        except Exception:
            logger.warning("Failed to revoke a rejected installation token", exc_info=True)


class _RetryableGitHubRead(RuntimeError):
    pass


class HttpGitHubAppClient:
    def __init__(
        self,
        app_id: int,
        private_key: str,
        *,
        client: httpx.Client | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self._app_id = app_id
        self._private_key = private_key
        self._client = client or httpx.Client(timeout=HTTP_TIMEOUT)
        self._now = now or (lambda: datetime.now(UTC))

    def find_installation(self, full_name: str) -> int:
        response = self._get(
            f"/repos/{full_name}/installation",
            token=self._app_jwt(),
        )
        if response.status_code == 404:
            raise BrokerError(
                "app_not_installed",
                404,
                f"Install the lgtmaybe GitHub App on this repository: {INSTALL_URL}",
            )
        _raise_github_error(response, "Could not verify the lgtmaybe App installation.")
        return _json_integer(response, "id")

    def create_token(
        self,
        installation_id: int,
        repository_id: int,
        permissions: dict[str, str],
    ) -> InstallationToken:
        response = self._request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            token=self._app_jwt(),
            json={"repository_ids": [repository_id], "permissions": permissions},
        )
        _raise_github_error(response, "Could not create a scoped GitHub App token.")
        payload = _json_object(response)
        raw_token = payload.get("token")
        raw_expiry = payload.get("expires_at")
        if not isinstance(raw_token, str) or not isinstance(raw_expiry, str):
            raise BrokerError("github_response", 502, "GitHub returned an invalid token response.")
        return InstallationToken(
            token=raw_token,
            expires_at=datetime.fromisoformat(raw_expiry.replace("Z", "+00:00")),
        )

    def get_repository(self, full_name: str, token: str) -> GitHubRepository:
        response = self._get(f"/repos/{full_name}", token=token)
        _raise_github_error(response, "Could not verify repository identity.")
        payload = _json_object(response)
        owner = payload.get("owner")
        if not isinstance(owner, dict):
            raise BrokerError("github_response", 502, "GitHub returned invalid repository data.")
        try:
            return GitHubRepository(
                id=int(payload["id"]),
                full_name=str(payload["full_name"]),
                owner_id=int(owner["id"]),
                default_branch=str(payload["default_branch"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BrokerError(
                "github_response", 502, "GitHub returned invalid repository data."
            ) from exc

    def revoke_token(self, token: str) -> None:
        response = self._request("DELETE", "/installation/token", token=token)
        if response.status_code != 204:
            _raise_github_error(response, "Could not revoke the GitHub App token.")

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, _RetryableGitHubRead)),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.1, max=1.0),
        reraise=True,
    )
    def _get(self, path: str, *, token: str) -> httpx.Response:
        response = self._request("GET", path, token=token)
        if response.status_code >= 500:
            raise _RetryableGitHubRead(f"GitHub read returned {response.status_code}")
        return response

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        json: dict[str, object] | None = None,
    ) -> httpx.Response:
        return self._client.request(
            method,
            f"{GITHUB_API}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
            json=json,
            timeout=HTTP_TIMEOUT,
        )

    def _app_jwt(self) -> str:
        now = self._now()
        return jwt.encode(
            {
                "iat": int((now - timedelta(seconds=60)).timestamp()),
                "exp": int((now + timedelta(minutes=9)).timestamp()),
                "iss": str(self._app_id),
            },
            self._private_key,
            algorithm="RS256",
        )


def _required_string(raw: Mapping[str, object], key: str) -> str:
    value = raw[key]
    if not isinstance(value, str) or not value:
        raise TypeError(key)
    return value


def _required_integer(raw: Mapping[str, object], key: str) -> int:
    value = _required_string(raw, key)
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(key)
    return parsed


def _json_object(response: httpx.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise BrokerError("github_response", 502, "GitHub returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise BrokerError("github_response", 502, "GitHub returned invalid JSON.")
    return payload


def _json_integer(response: httpx.Response, key: str) -> int:
    try:
        return int(_json_object(response)[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise BrokerError("github_response", 502, "GitHub returned invalid identity data.") from exc


def _raise_github_error(response: httpx.Response, message: str) -> None:
    if response.is_success:
        return
    status_code = 503 if response.status_code >= 500 else 502
    raise BrokerError("github_api", status_code, message)
