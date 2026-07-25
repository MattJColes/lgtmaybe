from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import boto3
from aws_lambda_powertools import Logger

if TYPE_CHECKING or __package__:
    from .broker import BrokerError, HttpGitHubAppClient, IdentityBroker, PyJwtOidcVerifier
else:
    from broker import BrokerError, HttpGitHubAppClient, IdentityBroker, PyJwtOidcVerifier

logger = Logger(service="github-app-identity")
_broker: IdentityBroker | None = None


def handler(event: dict[str, Any], context: object) -> dict[str, object]:
    del context
    try:
        token = _bearer_token(event)
        result = _get_broker().exchange(token)
    except BrokerError as exc:
        logger.warning("Identity exchange rejected", code=exc.code, status=exc.status_code)
        return _response(exc.status_code, {"code": exc.code, "message": str(exc)})
    except Exception:
        logger.exception("Identity exchange failed")
        return _response(
            503,
            {
                "code": "broker_unavailable",
                "message": "lgtmaybe identity service is unavailable; retry the workflow later.",
            },
        )

    return _response(
        200,
        {
            "token": result.token,
            "expires_at": result.expires_at.isoformat().replace("+00:00", "Z"),
        },
    )


def _bearer_token(event: dict[str, Any]) -> str:
    headers = event.get("headers")
    if not isinstance(headers, dict):
        raise BrokerError("invalid_request", 401, "Authorization bearer token is required.")
    authorization = next(
        (
            value
            for key, value in headers.items()
            if isinstance(key, str) and key.casefold() == "authorization"
        ),
        None,
    )
    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        raise BrokerError("invalid_request", 401, "Authorization bearer token is required.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise BrokerError("invalid_request", 401, "Authorization bearer token is required.")
    return token


def _get_broker() -> IdentityBroker:
    global _broker
    if _broker is None:
        app_id = int(os.environ["GITHUB_APP_ID"])
        secret_arn = os.environ["APP_PRIVATE_KEY_SECRET_ARN"]
        secret = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
        private_key = _private_key(secret)
        _broker = IdentityBroker(
            PyJwtOidcVerifier(),
            HttpGitHubAppClient(app_id, private_key),
        )
    return _broker


def _private_key(secret: dict[str, Any]) -> str:
    raw = secret.get("SecretString")
    if not isinstance(raw, str) or not raw:
        raise RuntimeError("GitHub App private-key secret is empty")
    if raw.lstrip().startswith("{"):
        payload = json.loads(raw)
        raw = payload.get("private_key")
    if not isinstance(raw, str) or "PRIVATE KEY" not in raw:
        raise RuntimeError("GitHub App private-key secret is invalid")
    return raw


def _response(status_code: int, body: dict[str, object]) -> dict[str, object]:
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
        },
        "body": json.dumps(body, separators=(",", ":")),
    }
