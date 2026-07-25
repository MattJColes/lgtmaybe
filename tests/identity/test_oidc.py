from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from services.github_app_identity.broker import (
    OIDC_AUDIENCE,
    OIDC_ISSUER,
    BrokerError,
    PyJwtOidcVerifier,
)


class StaticJwksClient:
    def __init__(self, key: object):
        self.key = key

    def get_signing_key_from_jwt(self, token: str) -> object:
        assert token
        return type("SigningKey", (), {"key": self.key})()


def _keys() -> tuple[object, object]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _payload(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "iss": OIDC_ISSUER,
        "aud": OIDC_AUDIENCE,
        "iat": now,
        "nbf": now - timedelta(seconds=5),
        "exp": now + timedelta(minutes=5),
        "jti": "request-1",
        "repository": "octo-org/octo-repo",
        "repository_id": "456",
        "repository_owner_id": "123",
        "event_name": "pull_request_target",
        "workflow_ref": "octo-org/octo-repo/.github/workflows/lgtmaybe.yml@refs/heads/main",
    }
    payload.update(overrides)
    return payload


def test_verifier_accepts_a_valid_github_oidc_token() -> None:
    private_key, public_key = _keys()
    token = jwt.encode(_payload(), private_key, algorithm="RS256", headers={"kid": "test"})
    verifier = PyJwtOidcVerifier(jwks_client=StaticJwksClient(public_key))

    claims = verifier.verify(token)

    assert claims["repository_id"] == "456"


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://attacker.example"},
        {"aud": "https://attacker.example"},
        {"exp": datetime.now(UTC) - timedelta(seconds=1)},
        {"nbf": datetime.now(UTC) + timedelta(minutes=5)},
    ],
)
def test_verifier_rejects_invalid_standard_claims(overrides: dict[str, object]) -> None:
    private_key, public_key = _keys()
    token = jwt.encode(
        _payload(**overrides), private_key, algorithm="RS256", headers={"kid": "test"}
    )
    verifier = PyJwtOidcVerifier(jwks_client=StaticJwksClient(public_key))

    with pytest.raises(BrokerError) as raised:
        verifier.verify(token)

    assert raised.value.code == "invalid_oidc"


def test_verifier_rejects_an_invalid_signature() -> None:
    signer, _ = _keys()
    _, trusted_public_key = _keys()
    token = jwt.encode(_payload(), signer, algorithm="RS256", headers={"kid": "test"})
    verifier = PyJwtOidcVerifier(jwks_client=StaticJwksClient(trusted_public_key))

    with pytest.raises(BrokerError) as raised:
        verifier.verify(token)

    assert raised.value.code == "invalid_oidc"
