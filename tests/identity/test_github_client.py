from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from services.github_app_identity.broker import (
    TOKEN_PERMISSIONS,
    BrokerError,
    HttpGitHubAppClient,
)


def _private_key() -> tuple[str, object]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return pem, key.public_key()


def test_client_mints_a_repository_and_permission_scoped_token() -> None:
    private_key, public_key = _private_key()
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/installation"):
            app_jwt = request.headers["Authorization"].removeprefix("Bearer ")
            claims = jwt.decode(
                app_jwt,
                public_key,
                algorithms=["RS256"],
                options={"verify_exp": False, "verify_iat": False},
            )
            assert claims["iss"] == "3987976"
            return httpx.Response(200, json={"id": 789})
        if request.url.path.endswith("/access_tokens"):
            assert json.loads(request.content) == {
                "repository_ids": [456],
                "permissions": TOKEN_PERMISSIONS,
            }
            return httpx.Response(
                201,
                json={"token": "ghs_scoped", "expires_at": "2026-07-25T10:00:00Z"},
            )
        if request.url.path == "/repos/octo-org/octo-repo":
            assert request.headers["Authorization"] == "Bearer ghs_scoped"
            return httpx.Response(
                200,
                json={
                    "id": 456,
                    "full_name": "octo-org/octo-repo",
                    "owner": {"id": 123},
                    "default_branch": "main",
                },
            )
        raise AssertionError(request.url)

    client = HttpGitHubAppClient(
        3987976,
        private_key,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        now=lambda: datetime(2026, 7, 25, tzinfo=UTC),
    )

    installation_id = client.find_installation("octo-org/octo-repo")
    token = client.create_token(installation_id, 456, TOKEN_PERMISSIONS.copy())
    repository = client.get_repository("octo-org/octo-repo", token.token)

    assert repository.id == 456
    assert len(requests) == 3
    assert all(request.extensions["timeout"]["read"] == 5.0 for request in requests)


def test_safe_github_reads_retry_transient_failures() -> None:
    private_key, _ = _private_key()
    attempts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        status = 200 if attempts == 3 else 502
        return httpx.Response(status, json={"id": 789})

    client = HttpGitHubAppClient(
        3987976,
        private_key,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    assert client.find_installation("octo-org/octo-repo") == 789
    assert attempts == 3


def test_token_creation_is_not_retried() -> None:
    private_key, _ = _private_key()
    attempts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(502, json={"message": "try later"})

    client = HttpGitHubAppClient(
        3987976,
        private_key,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    with pytest.raises(BrokerError):
        client.create_token(789, 456, TOKEN_PERMISSIONS.copy())

    assert attempts == 1


def test_missing_installation_points_to_the_public_app() -> None:
    private_key, _ = _private_key()
    client = HttpGitHubAppClient(
        3987976,
        private_key,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(404, json={}))
        ),
    )

    with pytest.raises(BrokerError, match="github.com/apps/lgtmaybe/installations/new"):
        client.find_installation("octo-org/octo-repo")
