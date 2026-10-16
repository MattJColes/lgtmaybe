"""Detect what a first-run ``lgtmaybe config init`` can offer without asking.

Looks for provider API keys in the environment, ambient AWS / GCP / Azure
credentials where that is cheap to tell (env vars and well-known files, never
an SDK call), and a running ollama (``GET /api/tags``, which also lists the
pulled models). Every probe is injectable so tests never touch the network or
the developer's own credential files.

Only the NAME of a key's variable is ever reported, never its value, and
nothing detected here is written anywhere: ``config init`` persists the chosen
provider and model, and keys stay in the environment.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from lgtmaybe.core.models import Provider
from lgtmaybe.providers import chatgpt
from lgtmaybe.providers.credentials import default_gcp_probe
from lgtmaybe.providers.factory import DEFAULT_OLLAMA_BASE

#: A current model to suggest per provider. Kept to IDs the how-to guides
#: already recommend (`tests/config/test_detect.py` checks), so the wizard and
#: the docs move together under the "model IDs kept current" rule. None means
#: the model is a name the user chose (an Azure deployment, whatever an
#: OpenAI-compatible server serves), so there is nothing sensible to guess.
SUGGESTED_MODELS: dict[Provider, str | None] = {
    Provider.openai: "gpt-5.5",
    Provider.openrouter: "google/gemini-3.8-flash",
    Provider.anthropic: "claude-sonnet-4-6",
    Provider.bedrock: "us.anthropic.claude-sonnet-4-6",
    Provider.vertex: "gemini-3.8-flash",
    Provider.azure: None,
    Provider.ollama: "qwen3.6:27b",
    Provider.openai_compatible: None,
    Provider.zai: "glm-4.6",
    Provider.chatgpt: "gpt-5.5",
}

# Env vars whose presence means "this provider's key is set", in the order they
# are offered. An exported key is the strongest signal of intent, so these lead.
_KEY_VARS: tuple[tuple[str, Provider], ...] = (
    ("ANTHROPIC_API_KEY", Provider.anthropic),
    ("OPENAI_API_KEY", Provider.openai),
    ("OPENROUTER_API_KEY", Provider.openrouter),
    ("ZAI_API_KEY", Provider.zai),
)

_AWS_ENV = (
    "AWS_ACCESS_KEY_ID",
    "AWS_PROFILE",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
)

# Long enough for a local server to answer, short enough that a machine with no
# ollama does not notice the wizard waited for it.
_OLLAMA_PROBE_TIMEOUT = 0.5


@dataclass(frozen=True)
class Detection:
    """One provider the environment looks ready for, and why."""

    provider: Provider
    reason: str
    # Models a running ollama has pulled (empty for every other provider).
    models: tuple[str, ...] = ()
    # The endpoint the detection found, when the provider needs one.
    api_base: str | None = None


def ollama_models(
    base: str = DEFAULT_OLLAMA_BASE,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[str, ...] | None:
    """The models a running ollama at ``base`` has pulled; None when unreachable.

    An answer that is not the expected shape still means a server is running,
    so it is an empty tuple rather than None.
    """
    url = f"{base.rstrip('/')}/api/tags"
    try:
        with httpx.Client(timeout=_OLLAMA_PROBE_TIMEOUT, transport=transport) as client:
            response = client.get(url)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        payload: Any = response.json()
    except ValueError:
        return ()
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return ()
    return tuple(
        str(item["name"]) for item in models if isinstance(item, dict) and item.get("name")
    )


def aws_credentials_present(env: Mapping[str, str], *, home: Path | None = None) -> bool:
    """Ambient AWS credentials: the SDK's env vars or its shared config files."""
    if any(env.get(var) for var in _AWS_ENV):
        return True
    root = (home or Path.home()) / ".aws"
    files = (
        Path(env.get("AWS_SHARED_CREDENTIALS_FILE") or root / "credentials"),
        Path(env.get("AWS_CONFIG_FILE") or root / "config"),
    )
    return any(path.is_file() for path in files)


def detect(
    *,
    env: Mapping[str, str] | None = None,
    probe_ollama: Callable[[str], tuple[str, ...] | None] | None = None,
    aws_probe: Callable[[Mapping[str, str]], bool] = aws_credentials_present,
    gcp_probe: Callable[[], bool] = default_gcp_probe,
    chatgpt_probe: Callable[[Mapping[str, str]], bool] = chatgpt.signed_in,
) -> list[Detection]:
    """Every provider the environment looks ready for, strongest signal first.

    Order: exported API keys, then a ChatGPT sign-in (as deliberate as an
    exported key), then a running ollama, then the cloud providers
    whose ambient credentials are only weak evidence (an ``~/.aws`` set up for
    another project says little about Bedrock model access).
    """
    env = os.environ if env is None else env
    probe = probe_ollama if probe_ollama is not None else ollama_models
    found = [Detection(provider, var) for var, provider in _KEY_VARS if env.get(var)]
    if chatgpt_probe(env):
        found.append(Detection(Provider.chatgpt, "ChatGPT sign-in from 'lgtmaybe login chatgpt'"))

    models = probe(DEFAULT_OLLAMA_BASE)
    if models is not None:
        count = f"{len(models)} model{'' if len(models) == 1 else 's'} pulled"
        found.append(
            Detection(
                Provider.ollama,
                f"ollama running at {DEFAULT_OLLAMA_BASE} ({count})",
                models=models,
                api_base=DEFAULT_OLLAMA_BASE,
            )
        )

    azure_base = env.get("AZURE_API_BASE")
    if azure_base:
        found.append(Detection(Provider.azure, "AZURE_API_BASE", api_base=azure_base))
    if aws_probe(env):
        found.append(Detection(Provider.bedrock, "AWS credentials"))
    if gcp_probe():
        found.append(Detection(Provider.vertex, "Google Cloud credentials"))
    return found


def suggest_model(provider: Provider, pulled: tuple[str, ...] = ()) -> str | None:
    """The model to offer as the default for ``provider``.

    For ollama, a suggestion only helps if it is pulled: prefer the table's
    model when it is, else the first pulled model, else the table's (which the
    wizard then tells the user to pull).
    """
    suggested = SUGGESTED_MODELS[provider]
    if pulled and suggested not in pulled:
        return pulled[0]
    return suggested
