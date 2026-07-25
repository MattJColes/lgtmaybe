from __future__ import annotations

import json
from datetime import UTC, datetime

from services.github_app_identity import handler
from services.github_app_identity.broker import BrokerError, InstallationToken


class SuccessfulBroker:
    def exchange(self, token: str) -> InstallationToken:
        assert token == "oidc-token"
        return InstallationToken(
            token="ghs_installation_token",
            expires_at=datetime(2026, 7, 25, 10, 0, tzinfo=UTC),
        )


class RejectedBroker:
    def exchange(self, token: str) -> InstallationToken:
        raise BrokerError("app_not_installed", 404, "Install the lgtmaybe GitHub App.")


def test_handler_returns_the_scoped_token_without_cacheable_headers(monkeypatch) -> None:
    monkeypatch.setattr(handler, "_broker", SuccessfulBroker())

    response = handler.handler(
        {"headers": {"authorization": "Bearer oidc-token"}},
        context=None,
    )

    assert response["statusCode"] == 200
    assert response["headers"]["cache-control"] == "no-store"
    assert json.loads(response["body"]) == {
        "token": "ghs_installation_token",
        "expires_at": "2026-07-25T10:00:00Z",
    }


def test_handler_returns_an_actionable_sanitized_rejection(monkeypatch) -> None:
    monkeypatch.setattr(handler, "_broker", RejectedBroker())

    response = handler.handler(
        {"headers": {"Authorization": "Bearer oidc-token"}},
        context=None,
    )

    assert response["statusCode"] == 404
    assert json.loads(response["body"]) == {
        "code": "app_not_installed",
        "message": "Install the lgtmaybe GitHub App.",
    }
    assert "oidc-token" not in response["body"]


def test_handler_rejects_a_missing_bearer_token() -> None:
    response = handler.handler({"headers": {}}, context=None)

    assert response["statusCode"] == 401
    assert json.loads(response["body"])["code"] == "invalid_request"
