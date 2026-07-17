"""providers — public surface for the provider track."""

from typing import Any

from lgtmaybe.providers.credentials import AuthConfig, resolve_credentials
from lgtmaybe.providers.factory import build_provider

__all__ = [
    "AuthConfig",
    "LiteLLMProvider",
    "build_provider",
    "resolve_credentials",
]


def __getattr__(name: str) -> Any:
    # LiteLLMProvider is resolved lazily (PEP 562): importing it pulls in
    # litellm, whose multi-second import would otherwise be paid by every CLI
    # command — including ones that never talk to a model.
    if name == "LiteLLMProvider":
        from lgtmaybe.providers.litellm_provider import LiteLLMProvider

        return LiteLLMProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
