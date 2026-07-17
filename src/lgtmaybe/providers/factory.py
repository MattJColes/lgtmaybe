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

from typing import TYPE_CHECKING, Any

from lgtmaybe.core.models import Provider
from lgtmaybe.providers.constants import DEFAULT_OLLAMA_BASE

if TYPE_CHECKING:
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


def cheaper_reflect_sibling(provider: Provider, model: str) -> str | None:
    """A cheaper, faster sibling of *model* to default ``reflect_model`` to.

    Used by the fast preset: the reflection audit re-reads the diff plus every
    finding, and a small sibling judges keep/drop well — the strong model's
    depth is spent on finding, not re-checking. Deliberately conservative:
    only providers whose small-model naming is stable get a mapping (anthropic
    haiku, openai mini), matched on the family name so a dated or versioned id
    still resolves. Everything else — bedrock/vertex model ids embed
    region/version schemes that drift, ollama is whatever the user pulled —
    returns None and reflection keeps using the review model, exactly as when
    the flag is unset. A wrong guess here would 404 every reflection pass, so
    "no mapping" beats a clever one.
    """
    name = model.lower()
    if provider is Provider.anthropic and ("sonnet" in name or "opus" in name):
        return "claude-haiku-4-5"
    if (
        provider is Provider.openai
        and name.startswith("gpt-5")
        and "mini" not in name
        and "nano" not in name
    ):
        return "gpt-5-mini"
    return None


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
    prompt_cache: bool = False,
    **extra_opts: Any,
) -> LiteLLMProvider:
    """Build a configured LiteLLMProvider for the given provider and model.

    ``timeout`` of ``None`` resolves to a provider-aware default
    (:func:`default_timeout_for`) — so ollama always gets a long timeout without
    the caller having to ask. An explicit value is honoured as-is.
    """
    # litellm's import is multi-second; deferring it here keeps `import
    # lgtmaybe.cli` fast for commands that never build a provider (config, help).
    from lgtmaybe.providers.litellm_provider import LiteLLMProvider

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
        prompt_cache=prompt_cache,
        **opts,
    )
