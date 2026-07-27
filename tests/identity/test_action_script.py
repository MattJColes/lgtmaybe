from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "github-app-identity.py"


def _module():
    spec = importlib.util.spec_from_file_location("github_app_identity_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, payload: dict[str, object], status: int = 200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_validation_accepts_default_actions_identity() -> None:
    module = _module()

    module.validate({"GITHUB_IDENTITY": "actions", "APP_ID": "", "APP_PRIVATE_KEY": ""})


def test_validation_rejects_public_and_self_managed_identity_together() -> None:
    module = _module()

    with pytest.raises(module.IdentitySetupError, match="Choose either"):
        module.validate(
            {
                "GITHUB_IDENTITY": "lgtmaybe",
                "APP_ID": "123",
                "APP_PRIVATE_KEY": "secret",
            }
        )


def test_validation_rejects_public_identity_for_check_runs() -> None:
    module = _module()

    with pytest.raises(module.IdentitySetupError, match="Checks: write"):
        module.validate(
            {
                "GITHUB_IDENTITY": "lgtmaybe",
                "APP_ID": "",
                "APP_PRIVATE_KEY": "",
                "FAIL_ON": "high",
            }
        )


def test_exchange_requests_oidc_and_masks_the_brokered_token(tmp_path: Path) -> None:
    module = _module()
    requests: list[Request] = []

    def open_request(request: Request, *, timeout: float):
        requests.append(request)
        assert timeout == 10.0
        if len(requests) == 1:
            return Response({"value": "oidc-token"})
        return Response({"token": "ghs_scoped", "expires_at": "2026-07-25T10:00:00Z"})

    output = tmp_path / "github-output"
    stdout = io.StringIO()
    module.exchange(
        {
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.example/token?x=1",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
            "IDENTITY_BROKER_URL": "https://identity.example/token",
            "GITHUB_OUTPUT": str(output),
        },
        opener=open_request,
        stdout=stdout,
    )

    assert requests[0].full_url.endswith(
        "&audience=https%3A%2F%2Flgtmaybe.coles.codes%2Fgithub-app-identity"
    )
    assert requests[0].headers["Authorization"] == "Bearer request-token"
    assert requests[1].method == "POST"
    assert requests[1].headers["Authorization"] == "Bearer oidc-token"
    assert output.read_text(encoding="utf-8") == "token=ghs_scoped\n"
    assert stdout.getvalue() == "::add-mask::ghs_scoped\n"


def test_exchange_explains_missing_id_token_permission() -> None:
    module = _module()

    with pytest.raises(module.IdentitySetupError, match="id-token: write"):
        module.exchange(
            {"IDENTITY_BROKER_URL": "https://identity.example/token"},
            opener=lambda request, timeout: Response({}),
            stdout=io.StringIO(),
        )


def test_exchange_preserves_the_broker_setup_message() -> None:
    module = _module()
    error = HTTPError(
        "https://identity.example/token",
        404,
        "Not Found",
        {},
        io.BytesIO(
            json.dumps(
                {
                    "code": "app_not_installed",
                    "message": "Install the lgtmaybe GitHub App.",
                }
            ).encode()
        ),
    )
    calls = 0

    def open_request(request: Request, *, timeout: float):
        nonlocal calls
        calls += 1
        if calls == 1:
            return Response({"value": "oidc-token"})
        raise error

    with pytest.raises(module.IdentitySetupError, match="Install the lgtmaybe GitHub App"):
        module.exchange(
            {
                "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.example/token?x=1",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
                "IDENTITY_BROKER_URL": "https://identity.example/token",
                "GITHUB_OUTPUT": "unused",
            },
            opener=open_request,
            stdout=io.StringIO(),
        )


def test_revoke_is_best_effort_and_never_prints_the_token() -> None:
    module = _module()
    request_seen: list[Request] = []
    stdout = io.StringIO()

    def open_request(request: Request, *, timeout: float):
        request_seen.append(request)
        return Response({}, status=204)

    module.revoke(
        {"LGTMAYBE_TOKEN": "ghs_scoped"},
        opener=open_request,
        stdout=stdout,
    )

    assert request_seen[0].method == "DELETE"
    assert request_seen[0].headers["Authorization"] == "Bearer ghs_scoped"
    assert "ghs_scoped" not in stdout.getvalue()


# ---------------------------------------------------------------------------
# events that cannot run from the default branch
# ---------------------------------------------------------------------------


def test_exchange_skips_an_event_that_cannot_mint_branded_identity(tmp_path: Path) -> None:
    """`pull_request_review_comment` runs from the PR branch, not the default
    branch, so the broker's default-branch assertion can never be satisfied.

    That guard is correct — it is what stops a contributor editing the workflow
    on their own branch and minting an App token — so the client must not ask.
    Asking anyway failed the whole review job every time somebody replied to a
    finding, which is how `answer_replies` came to be dead on GitHub.
    """
    module = _module()
    output = tmp_path / "github-output"
    stdout = io.StringIO()

    def open_request(request: Request, *, timeout: float):
        raise AssertionError("no identity exchange should be attempted")

    module.exchange(
        {
            "GITHUB_EVENT_NAME": "pull_request_review_comment",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.example/token?x=1",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
            "IDENTITY_BROKER_URL": "https://identity.example/token",
            "GITHUB_OUTPUT": str(output),
        },
        opener=open_request,
        stdout=stdout,
    )

    # No token output — action.yml's `|| inputs.github_token` chain then falls
    # back to the workflow token, and the revoke step stays skipped.
    assert not output.exists() or "token=" not in output.read_text(encoding="utf-8")
    # The downgrade is announced, never silent.
    assert "::notice::" in stdout.getvalue()
    assert "github-actions[bot]" in stdout.getvalue()


def test_exchange_still_runs_for_events_that_use_the_default_branch(tmp_path: Path) -> None:
    """issue_comment and pull_request_target do run from the default branch and
    mint branded identity normally — the skip must not widen to them."""
    module = _module()

    for event in ("issue_comment", "pull_request_target", ""):
        calls: list[Request] = []

        def open_request(request: Request, *, timeout: float, _c: list[Request] = calls):
            _c.append(request)
            if len(_c) == 1:
                return Response({"value": "oidc-token"})
            return Response({"token": "ghs_scoped"})

        output = tmp_path / f"out-{event or 'unset'}"
        module.exchange(
            {
                "GITHUB_EVENT_NAME": event,
                "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.example/token?x=1",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
                "IDENTITY_BROKER_URL": "https://identity.example/token",
                "GITHUB_OUTPUT": str(output),
            },
            opener=open_request,
            stdout=io.StringIO(),
        )

        assert output.read_text(encoding="utf-8") == "token=ghs_scoped\n", event
