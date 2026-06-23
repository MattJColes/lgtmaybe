"""Provider factory — maps (Provider, model) → configured LiteLLMProvider.

litellm model-string conventions:
  openai     → openai/<model>
  anthropic  → anthropic/<model>
  openrouter → openrouter/<model>
  bedrock    → bedrock/<model>
  vertex     → vertex_ai/<model>
  azure      → azure/<model>   (+ api_base = resource endpoint)
  ollama     → ollama/<model>  (+ api_base)
  openai-compatible → openai/<model>  (+ api_base = custom endpoint)
  zai        → zai/<model>     (GLM / Zhipu AI; optional api_base override)
"""

from __future__ import annotations

from typing import Any

from lgtmaybe.core.models import Provider
from lgtmaybe.providers.constants import DEFAULT_OLLAMA_BASE
from lgtmaybe.providers.litellm_provider import LiteLLMProvider

_PREFIXES: dict[Provider, str] = {
    Provider.openai: "openai",
    Provider.anthropic: "anthropic",
    Provider.openrouter: "openrouter",
    Provider.bedrock: "bedrock",
    Provider.vertex: "vertex_ai",
    Provider.azure: "azure",
    Provider.ollama: "ollama",
    # OpenAI-compatible servers (DeepSeek, llama.cpp, LM Studio, vLLM) ride the
    # openai route; the custom endpoint comes through api_base.
    Provider.openai_compatible: "openai",
    # GLM / Zhipu AI on litellm's native zai/ route (e.g. zai/glm-4.6).
    Provider.zai: "zai",
}

# Default per-request timeout (seconds) when the caller doesn't set one. Local
# models are slow — and the per-category fan-out runs them serially — so the
# providers that can front a local server (ollama, and openai-compatible pointing
# at llama.cpp / LM Studio / vLLM) get a generous default; cloud providers respond
# fast. An explicit --timeout always wins, so a fast cloud openai-compatible
# endpoint (e.g. DeepSeek) can dial it down.
_LOCAL_TIMEOUT = 300
_CLOUD_TIMEOUT = 60

# Providers whose endpoint may be a slow, locally hosted model.
_LOCAL_CAPABLE = frozenset({Provider.ollama, Provider.openai_compatible})

# Ollama context window. Big enough to hold a real review prompt + diff + the
# emitted findings; ollama's own default (~4k) truncates the output to a stub.
# Sized to hold a real multi-file diff review — 16k was tight enough that a real
# review could overrun it and get truncated.
_OLLAMA_NUM_CTX = 32768


def default_timeout_for(provider: Provider) -> int:
    """The auto timeout (seconds) for a provider when none is given explicitly."""
    return _LOCAL_TIMEOUT if provider in _LOCAL_CAPABLE else _CLOUD_TIMEOUT


def litellm_model_string(provider: Provider, model: str) -> str:
    """Return the litellm model string for the given provider and model name."""
    return f"{_PREFIXES[provider]}/{model}"


def build_provider(
    provider: Provider,
    model: str,
    *,
    api_key: str | None = None,
    api_base: str | None = None,
    azure_ad_token: str | None = None,
    fallback_model: str | None = None,
    timeout: int | None = None,
    **extra_opts: Any,
) -> LiteLLMProvider:
    """Build a configured LiteLLMProvider for the given provider and model.

    ``timeout`` of ``None`` resolves to a provider-aware default
    (:func:`default_timeout_for`) — so ollama always gets a long timeout without
    the caller having to ask. An explicit value is honoured as-is.
    """
    resolved_model = litellm_model_string(provider, model)
    resolved_fallback = litellm_model_string(provider, fallback_model) if fallback_model else None
    opts: dict[str, Any] = dict(extra_opts)

    opts["timeout"] = timeout if timeout is not None else default_timeout_for(provider)

    if api_key is not None:
        opts["api_key"] = api_key

    # Keyless Azure: an Azure AD bearer token instead of a static key.
    if azure_ad_token is not None:
        opts["azure_ad_token"] = azure_ad_token

    is_ollama = provider is Provider.ollama
    if is_ollama:
        opts["api_base"] = api_base or DEFAULT_OLLAMA_BASE
        # Disable "thinking" for ollama models. Thinking models (qwen3.x) otherwise
        # route their whole answer to the reasoning channel and return EMPTY content
        # under structured output — so JSON-mode yields nothing to parse. With
        # think=False they emit the findings JSON directly.
        opts["think"] = False
        # Ollama's default context window (~4k) is smaller than a real review
        # prompt (system prompt + wrapped diff + context lines), which truncates
        # the output to a stub. Give it enough room to read the prompt AND emit the
        # findings. Overridable for very large diffs or memory-constrained hosts.
        opts.setdefault("num_ctx", _OLLAMA_NUM_CTX)
    elif api_base is not None:
        # Azure routes to a per-resource endpoint; any other provider that
        # supplies an explicit base (e.g. a proxy) is honoured too.
        opts["api_base"] = api_base

    return LiteLLMProvider(
        model=resolved_model,
        fallback_model=resolved_fallback,
        **opts,
    )
