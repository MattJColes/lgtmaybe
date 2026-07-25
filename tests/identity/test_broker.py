from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from services.github_app_identity.broker import (
    BrokerError,
    GitHubRepository,
    IdentityBroker,
    InstallationToken,
)

REPOSITORY = GitHubRepository(
    id=456,
    full_name="octo-org/octo-repo",
    owner_id=123,
    default_branch="main",
)


def _claims(**overrides: object) -> dict[str, object]:
    claims: dict[str, object] = {
        "repository": REPOSITORY.full_name,
        "repository_id": str(REPOSITORY.id),
        "repository_owner_id": str(REPOSITORY.owner_id),
        "event_name": "pull_request_target",
        "workflow_ref": f"{REPOSITORY.full_name}/.github/workflows/lgtmaybe.yml@refs/heads/main",
        "jti": "oidc-request-1",
    }
    claims.update(overrides)
    return claims


class FakeVerifier:
    def __init__(self, claims: dict[str, object] | None = None, error: BrokerError | None = None):
        self.claims = claims or _claims()
        self.error = error

    def verify(self, token: str) -> dict[str, object]:
        assert token == "oidc-token"
        if self.error:
            raise self.error
        return self.claims


class FakeGitHub:
    def __init__(
        self,
        repository: GitHubRepository = REPOSITORY,
        installation_id: int | None = 789,
    ):
        self.repository = repository
        self.installation_id = installation_id
        self.created_for: tuple[int, int, dict[str, str]] | None = None
        self.revoked: list[str] = []

    def find_installation(self, full_name: str) -> int:
        assert full_name
        if self.installation_id is None:
            raise BrokerError("app_not_installed", 404, "Install the lgtmaybe GitHub App.")
        return self.installation_id

    def create_token(
        self,
        installation_id: int,
        repository_id: int,
        permissions: dict[str, str],
    ) -> InstallationToken:
        self.created_for = (installation_id, repository_id, permissions)
        return InstallationToken(
            token="ghs_installation_token",
            expires_at=datetime.now(UTC) + timedelta(minutes=55),
        )

    def get_repository(self, full_name: str, token: str) -> GitHubRepository:
        assert full_name
        assert token == "ghs_installation_token"
        return self.repository

    def revoke_token(self, token: str) -> None:
        self.revoked.append(token)


def _broker(
    *,
    claims: dict[str, object] | None = None,
    verifier_error: BrokerError | None = None,
    github: FakeGitHub | None = None,
) -> tuple[IdentityBroker, FakeGitHub]:
    client = github or FakeGitHub()
    return IdentityBroker(FakeVerifier(claims, verifier_error), client), client


def test_valid_exchange_is_scoped_to_the_verified_repository() -> None:
    broker, github = _broker()

    result = broker.exchange("oidc-token")

    assert result.token == "ghs_installation_token"
    assert github.created_for == (
        789,
        REPOSITORY.id,
        {"contents": "read", "pull_requests": "write", "issues": "write"},
    )


@pytest.mark.parametrize(
    "reason",
    ["invalid_signature", "invalid_issuer", "invalid_audience", "expired", "not_yet_valid"],
)
def test_invalid_standard_oidc_claims_are_rejected(reason: str) -> None:
    error = BrokerError("invalid_oidc", 401, f"OIDC token rejected: {reason}")
    broker, github = _broker(verifier_error=error)

    with pytest.raises(BrokerError, match="OIDC token rejected") as raised:
        broker.exchange("oidc-token")

    assert raised.value.code == "invalid_oidc"
    assert github.created_for is None


@pytest.mark.parametrize("event_name", ["pull_request", "push", "workflow_dispatch"])
def test_non_base_safe_event_is_rejected(event_name: str) -> None:
    broker, github = _broker(claims=_claims(event_name=event_name))

    with pytest.raises(BrokerError) as raised:
        broker.exchange("oidc-token")

    assert raised.value.code == "untrusted_workflow"
    assert github.created_for is None


def test_non_default_branch_workflow_is_rejected_and_token_revoked() -> None:
    broker, github = _broker(
        claims=_claims(
            workflow_ref=f"{REPOSITORY.full_name}/.github/workflows/lgtmaybe.yml@refs/heads/dev"
        )
    )

    with pytest.raises(BrokerError) as raised:
        broker.exchange("oidc-token")

    assert raised.value.code == "untrusted_workflow"
    assert github.revoked == ["ghs_installation_token"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", "renamed-org/octo-repo"),
        ("repository_id", "999"),
        ("repository_owner_id", "999"),
    ],
)
def test_mutable_repository_mismatch_is_rejected_and_token_revoked(field: str, value: str) -> None:
    overrides: dict[str, object] = {field: value}
    if field == "repository":
        overrides["workflow_ref"] = f"{value}/.github/workflows/lgtmaybe.yml@refs/heads/main"
    broker, github = _broker(claims=_claims(**overrides))

    with pytest.raises(BrokerError) as raised:
        broker.exchange("oidc-token")

    assert raised.value.code == "repository_mismatch"
    assert github.revoked == ["ghs_installation_token"]


def test_missing_installation_returns_actionable_error() -> None:
    broker, github = _broker(github=FakeGitHub(installation_id=None))

    with pytest.raises(BrokerError, match="Install the lgtmaybe GitHub App") as raised:
        broker.exchange("oidc-token")

    assert raised.value.code == "app_not_installed"
    assert github.created_for is None


def test_logs_contain_identity_metadata_but_no_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker, _ = _broker()

    with caplog.at_level(logging.INFO):
        broker.exchange("oidc-token")

    log_output = caplog.text
    assert "456" in log_output
    assert "oidc-token" not in log_output
    assert "ghs_installation_token" not in log_output
