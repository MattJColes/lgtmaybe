"""ChatGPT subscription sign-in for the ``chatgpt`` provider.

litellm's ``chatgpt/`` route reaches the Codex backend with a ChatGPT Plus or
Pro subscription instead of an API key. It signs in the way the Codex CLI does
(an OAuth device code), keeps the tokens in a file and refreshes them itself.

``token_file`` answers "is there a sign-in?" without importing litellm, whose
import is multi-second, so it re-derives litellm's own path rule
(``tests/providers/test_chatgpt.py`` checks the two agree). ``login`` runs the
device-code sign-in for ``lgtmaybe login chatgpt``.

A review must never start that sign-in itself. litellm falls back to it from
any token lookup (``get_llm_provider`` included) once a stored sign-in is
missing or its refresh fails: it prints to stdout, which corrupts the MCP
server's stdio stream and ``--format json``, and polls for 15 minutes inside
every concurrent lens call. So a review installs ``forbid_interactive_login``
first, and ``refresh`` renews the token once, before the fan-out, rather than
letting six lens calls race to rotate one refresh token.

The token belongs to litellm's file, never lgtmaybe's config: like an API key,
it is not persisted anywhere lgtmaybe writes.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

# litellm's defaults (``litellm/llms/chatgpt/authenticator.py``).
_DEFAULT_TOKEN_DIR = "~/.config/litellm/chatgpt"
_DEFAULT_AUTH_FILE = "auth.json"


_RELOGIN_HINT = "Run 'lgtmaybe login chatgpt' to sign in again."


class ChatGPTLoginError(Exception):
    """The device-code sign-in did not complete."""


def token_file(env: Mapping[str, str] | None = None) -> Path:
    """Where litellm keeps the ChatGPT sign-in, honouring its env overrides."""
    env = os.environ if env is None else env
    directory = env.get("CHATGPT_TOKEN_DIR", os.path.expanduser(_DEFAULT_TOKEN_DIR))
    return Path(directory) / env.get("CHATGPT_AUTH_FILE", _DEFAULT_AUTH_FILE)


def signed_in(env: Mapping[str, str] | None = None) -> bool:
    """Whether a ChatGPT sign-in is stored (its tokens are litellm's to refresh)."""
    return token_file(env).is_file()


def login() -> Path:
    """Sign in with a device code, or refresh a stored sign-in; return the token file.

    litellm prints the verification URL and the code, then polls until the user
    approves it in a browser (it gives up after 15 minutes).
    """
    try:
        from litellm.llms.chatgpt.authenticator import Authenticator
        from litellm.llms.chatgpt.common_utils import ChatGPTAuthError
    except ImportError as exc:
        raise ChatGPTLoginError(
            "the installed litellm has no chatgpt route. Upgrade lgtmaybe (or litellm) "
            "to sign in with a ChatGPT subscription."
        ) from exc

    authenticator = Authenticator()
    try:
        authenticator.get_access_token()
    except ChatGPTAuthError as exc:
        raise ChatGPTLoginError(f"ChatGPT sign-in failed: {exc}") from exc
    return Path(authenticator.auth_file)


def forbid_interactive_login() -> None:
    """Make litellm's device-code fallback fail fast instead of prompting.

    Process-wide and idempotent. Only review paths call it: ``lgtmaybe login``
    runs in its own process and keeps litellm's prompt.

    It replaces a private litellm method, and a pip install is not held to the
    lockfile. So a litellm that no longer has the method fails loudly here:
    assigning a missing name would guard nothing and bring the prompt back.
    """
    from litellm.llms.chatgpt.authenticator import Authenticator
    from litellm.llms.chatgpt.common_utils import GetAccessTokenError

    if not callable(getattr(Authenticator, "_login_device_code", None)):
        raise ValueError(
            "this litellm version moved its ChatGPT sign-in prompt, so lgtmaybe cannot "
            "stop a review from starting it. Install the litellm version lgtmaybe "
            "pins, or use another provider."
        )

    def refuse(self: Authenticator) -> dict[str, str]:
        raise GetAccessTokenError(
            message=f"the ChatGPT sign-in is missing or has expired. {_RELOGIN_HINT}",
            status_code=401,
        )

    Authenticator._login_device_code = refuse  # type: ignore[method-assign]


def refresh() -> None:
    """Renew the stored sign-in now if it needs it; never prompt.

    Raises ValueError with the re-login hint when the sign-in cannot be used.
    """
    forbid_interactive_login()
    from litellm.llms.chatgpt.authenticator import Authenticator
    from litellm.llms.chatgpt.common_utils import ChatGPTAuthError

    try:
        Authenticator().get_access_token()
    except ChatGPTAuthError as exc:
        detail = str(exc)
        hint = "" if _RELOGIN_HINT in detail else f" {_RELOGIN_HINT}"
        raise ValueError(f"chatgpt sign-in unusable: {detail}{hint}") from exc
