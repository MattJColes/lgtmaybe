"""The chatgpt provider: a ChatGPT Plus/Pro subscription instead of an API key.

litellm's ``chatgpt/`` route signs in the way the Codex CLI does (an OAuth
device code), keeps the tokens in a file and refreshes them itself. What this
file pins down is the part lgtmaybe owns: finding that file without importing
litellm, failing with a clear "sign in" message when it is missing, the
``lgtmaybe login chatgpt`` command, and the route's quirk that its
capability list names ``response_format`` while its Codex transform strips it.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from pydantic import BaseModel

from lgtmaybe.cli import main
from lgtmaybe.core.models import Provider
from lgtmaybe.providers import chatgpt
from lgtmaybe.providers.credentials import AuthConfig, resolve_credentials
from lgtmaybe.providers.factory import build_provider, dropped_params, litellm_model_string


class _Findings(BaseModel):
    findings: list[str]


def _jwt(claims: dict[str, Any]) -> str:
    """An unsigned JWT: litellm only decodes the payload to read ``exp``."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("ascii")
    return f"header.{payload.rstrip('=')}.signature"


def _write_token(directory: Path, *, expires_in: int = 3600) -> Path:
    token = _jwt(
        {
            "exp": int(time.time()) + expires_in,
            "https://api.openai.com/auth": {"chatgpt_account_id": "acct-test"},
        }
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "auth.json"
    path.write_text(
        json.dumps(
            {
                "access_token": token,
                "refresh_token": "refresh",
                "id_token": token,
                "expires_at": int(time.time()) + expires_in,
            }
        ),
        encoding="utf-8",
    )
    return path


# --- where the sign-in lives -------------------------------------------------


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"CHATGPT_TOKEN_DIR": "/somewhere/tokens"},
        {"CHATGPT_TOKEN_DIR": "/somewhere/tokens", "CHATGPT_AUTH_FILE": "other.json"},
    ],
    ids=["default", "token-dir", "token-dir-and-file"],
)
def test_token_file_matches_where_litellm_reads_it(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The resolver finds the file without importing litellm, so it re-derives
    litellm's path. This catches the two drifting apart on an upgrade."""
    from litellm.llms.chatgpt.authenticator import Authenticator

    monkeypatch.delenv("CHATGPT_TOKEN_DIR", raising=False)
    monkeypatch.delenv("CHATGPT_AUTH_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    with patch.object(Authenticator, "_ensure_token_dir"):
        expected = Path(Authenticator().auth_file)

    assert chatgpt.token_file() == expected


def test_signed_in_reads_the_env_it_is_given(tmp_path: Path) -> None:
    env = {"CHATGPT_TOKEN_DIR": str(tmp_path / "tokens")}
    assert not chatgpt.signed_in(env)
    _write_token(tmp_path / "tokens")
    assert chatgpt.signed_in(env)


# --- credentials ---------------------------------------------------------------


def test_missing_sign_in_fails_with_the_login_command(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="lgtmaybe login chatgpt") as info:
        resolve_credentials(Provider.chatgpt)
    # Names where it looked, so a custom CHATGPT_TOKEN_DIR mistake is visible.
    assert str(chatgpt.token_file()) in str(info.value)


def test_a_stored_sign_in_resolves_with_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path / "tokens"))
    _write_token(tmp_path / "tokens")
    assert resolve_credentials(Provider.chatgpt) == AuthConfig()


# --- lgtmaybe login chatgpt ---------------------------------------------------


def test_login_runs_the_device_sign_in_and_names_the_token_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from litellm.llms.chatgpt.authenticator import Authenticator

    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path / "tokens"))

    def sign_in(self: Authenticator) -> str:
        _write_token(Path(self.token_dir))
        return "access"

    with patch.object(Authenticator, "get_access_token", sign_in):
        result = CliRunner().invoke(main, ["login", "chatgpt"])

    assert result.exit_code == 0, result.output
    assert "Signed in to ChatGPT" in result.output
    assert str(tmp_path / "tokens" / "auth.json") in result.output
    assert chatgpt.signed_in()


def test_a_failed_login_exits_non_zero_with_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    from litellm.llms.chatgpt.authenticator import Authenticator
    from litellm.llms.chatgpt.common_utils import GetAccessTokenError

    def refuse(self: Authenticator) -> str:
        raise GetAccessTokenError(
            message="Timed out waiting for device authorization", status_code=408
        )

    with patch.object(Authenticator, "get_access_token", refuse):
        result = CliRunner().invoke(main, ["login", "chatgpt"])

    assert result.exit_code != 0
    assert "Timed out waiting for device authorization" in result.output


# --- a review never prompts --------------------------------------------------


@pytest.fixture
def stored_sign_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path / "tokens"))
    return _write_token(tmp_path / "tokens")


def test_a_review_refuses_the_device_prompt_instead_of_printing_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """litellm falls back to an interactive sign-in from any token lookup. Under
    `lgtmaybe mcp` its stdout print would corrupt the JSON-RPC stream, and it
    polls for 15 minutes, so a review fails fast instead."""
    import litellm
    from litellm.llms.chatgpt.authenticator import Authenticator

    with patch.object(Authenticator, "_request_device_code") as device_code:
        build_provider(Provider.chatgpt, "gpt-5.5")
        with pytest.raises(Exception, match="lgtmaybe login chatgpt"):
            litellm.get_llm_provider(model="chatgpt/gpt-5.5")

    device_code.assert_not_called()
    assert capsys.readouterr().out == ""


def test_an_unrenewable_sign_in_fails_at_start_up_with_the_login_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from litellm.llms.chatgpt.authenticator import Authenticator
    from litellm.llms.chatgpt.common_utils import RefreshAccessTokenError

    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path / "tokens"))
    _write_token(tmp_path / "tokens", expires_in=-60)

    def revoked(_self: Authenticator, _token: str) -> dict[str, str]:
        raise RefreshAccessTokenError(message="refresh_token_reused", status_code=401)

    with (
        patch.object(Authenticator, "_refresh_tokens", revoked),
        patch.object(Authenticator, "_request_device_code") as device_code,
        pytest.raises(ValueError, match="lgtmaybe login chatgpt"),
    ):
        resolve_credentials(Provider.chatgpt)
    device_code.assert_not_called()


def test_an_unusable_sign_in_always_names_the_login_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hint comes from refresh() itself, not from whatever litellm's own
    error text happens to say."""
    from litellm.llms.chatgpt.authenticator import Authenticator
    from litellm.llms.chatgpt.common_utils import GetAccessTokenError

    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path / "tokens"))
    _write_token(tmp_path / "tokens")

    def broken(_self: Authenticator) -> str:
        raise GetAccessTokenError(message="token file unreadable", status_code=400)

    with (
        patch.object(Authenticator, "get_access_token", broken),
        pytest.raises(ValueError, match="token file unreadable") as info,
    ):
        resolve_credentials(Provider.chatgpt)
    assert "lgtmaybe login chatgpt" in str(info.value)


def test_a_hint_litellm_already_carries_is_not_repeated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard's own refusal already names the login command; refresh() must
    not append it a second time."""
    from litellm.llms.chatgpt.authenticator import Authenticator
    from litellm.llms.chatgpt.common_utils import GetAccessTokenError

    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path / "tokens"))
    _write_token(tmp_path / "tokens")

    def expired(_self: Authenticator) -> str:
        raise GetAccessTokenError(
            message=f"token expired. {chatgpt._RELOGIN_HINT}", status_code=401
        )

    with (
        patch.object(Authenticator, "get_access_token", expired),
        pytest.raises(ValueError, match="chatgpt sign-in unusable") as info,
    ):
        resolve_credentials(Provider.chatgpt)
    assert str(info.value).count("lgtmaybe login chatgpt") == 1


def test_the_guard_fails_loudly_if_litellm_renames_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pip installs are not held to our lock. If a newer litellm moves the
    device-code prompt, patching a missing name would silently guard nothing."""
    from litellm.llms.chatgpt.authenticator import Authenticator

    # raising=False: the test must also hold on a litellm that already lacks it.
    monkeypatch.delattr(Authenticator, "_login_device_code", raising=False)
    with pytest.raises(ValueError, match="litellm"):
        chatgpt.forbid_interactive_login()


def test_an_expiring_sign_in_is_renewed_once_up_front(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renewed before the lens fan-out, so concurrent calls never race to
    rotate one refresh token."""
    from litellm.llms.chatgpt.authenticator import Authenticator

    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path / "tokens"))
    _write_token(tmp_path / "tokens", expires_in=-60)
    renewed: list[str] = []

    def renew(self: Authenticator, token: str) -> dict[str, str]:
        renewed.append(token)
        _write_token(Path(self.token_dir))
        return {"access_token": "fresh"}

    with patch.object(Authenticator, "_refresh_tokens", renew):
        assert resolve_credentials(Provider.chatgpt) == AuthConfig()
    assert renewed == ["refresh"]


# --- request shaping -----------------------------------------------------------


def test_model_string_uses_the_chatgpt_route() -> None:
    assert litellm_model_string(Provider.chatgpt, "gpt-5.5") == "chatgpt/gpt-5.5"


def test_params_the_codex_transform_strips_are_reported_as_dropped() -> None:
    """litellm's list names these for the route, but its Codex transform never
    sends them, so a configured value must be announced as ignored."""
    dropped = dropped_params(Provider.chatgpt, "gpt-5.5", ["temperature", "max_tokens"])
    assert dropped == ["max_tokens", "temperature"]


@pytest.mark.usefixtures("stored_sign_in")
def test_the_schema_goes_as_a_forced_tool_call_up_front() -> None:
    """The real capability list says ``response_format`` is supported, which on
    its own would send the schema where the transform silently drops it."""
    from tests.providers.test_litellm_provider import _tool_call_response

    sent: list[dict[str, Any]] = []

    def capture(**kwargs: Any) -> Any:
        sent.append(kwargs)
        return _tool_call_response('{"findings": []}')

    built = build_provider(Provider.chatgpt, "gpt-5.5")
    with patch("litellm.completion", side_effect=capture):
        result = built.complete(
            [{"role": "user", "content": "hi"}], "gpt-5.5", response_format=_Findings
        )

    assert result.text == '{"findings": []}'
    assert len(sent) == 1
    assert "response_format" not in sent[0]
    assert sent[0]["tool_choice"]["function"]["name"] == "lgtmaybe_structured_output"
    assert not built.schema_dropped()


@pytest.mark.usefixtures("stored_sign_in")
def test_the_dropped_temperature_is_disclosed() -> None:
    from tests.providers.test_litellm_provider import _tool_call_response

    built = build_provider(Provider.chatgpt, "gpt-5.5", temperature=0.0)
    with patch("litellm.completion", return_value=_tool_call_response('{"findings": []}')):
        built.complete([{"role": "user", "content": "hi"}], "gpt-5.5", response_format=_Findings)

    assert built.temperature_dropped()


# --- the real wire, against a loopback stand-in for the Codex backend ----------


class _Backend(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]]

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(body)
        tool = body["tools"][0]["name"] if body.get("tools") else None
        if tool:
            item: dict[str, Any] = {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": tool,
                "arguments": json.dumps({"findings": ["from the wire"]}),
                "status": "completed",
            }
        else:
            item = {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "prose", "annotations": []}],
            }
        response = {
            "id": "resp_1",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": body.get("model"),
            "output": [item],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }
        event = {"type": "response.completed", "response": response}
        payload = f"event: response.completed\ndata: {json.dumps(event)}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: Any) -> None:
        return None


@pytest.fixture
def codex_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict]]:
    requests: list[dict[str, Any]] = []
    handler = type("Handler", (_Backend,), {"requests": requests})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", str(tmp_path / "tokens"))
    monkeypatch.setenv("CHATGPT_API_BASE", f"http://127.0.0.1:{server.server_port}")
    _write_token(tmp_path / "tokens")
    try:
        yield requests
    finally:
        server.shutdown()
        server.server_close()


def test_the_schema_reaches_the_codex_backend_and_the_answer_parses(
    codex_backend: list[dict[str, Any]],
) -> None:
    """Runs litellm's real chatgpt transform end to end, offline. If an upgrade
    stops forwarding tools, or starts forwarding a param listed as stripped,
    this is where it shows."""
    built = build_provider(Provider.chatgpt, "gpt-5.5", temperature=0.0)

    result = built.complete(
        [{"role": "system", "content": "review"}, {"role": "user", "content": "diff"}],
        "gpt-5.5",
        response_format=_Findings,
    )

    assert json.loads(result.text) == {"findings": ["from the wire"]}
    (request,) = codex_backend
    assert request["tool_choice"]["name"] == "lgtmaybe_structured_output"
    assert request["tools"][0]["parameters"]["required"] == ["findings"]
    # What the factory reports as stripped really is absent on the wire.
    assert "text" not in request
    assert "temperature" not in request
    assert "max_output_tokens" not in request
