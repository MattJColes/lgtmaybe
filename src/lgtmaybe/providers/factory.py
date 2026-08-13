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

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import Provider

if TYPE_CHECKING:
    from lgtmaybe.providers.litellm_provider import LiteLLMProvider

_log = get_logger(__name__)

DEFAULT_OLLAMA_BASE = "http://localhost:11434"
CLOUD_TIMEOUT = 600
OPENAI_COMPATIBLE_PLACEHOLDER_KEY = "lgtmaybe-no-key"

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

# Default per-request timeout (seconds) when the caller doesn't set one. Some
# providers can't be assumed fast: the ones that can front a slow local server
# (ollama, and openai-compatible pointing at llama.cpp / LM Studio / vLLM), and
# openrouter, a gateway to arbitrary third-party models — including reasoning
# models that routinely think slowly on a large diff. Those get a generous
# default; direct cloud providers get a shorter one that still leaves room
# for a reasoning model to think. An explicit --timeout always wins, so a
# fast endpoint can dial it down.
#
# Both are sized for the failure that matters: a timed-out lens call posts
# "results may be incomplete" with no findings, which a human reads as a clean
# review. Waiting longer for an answer beats reporting a confident nothing, so
# these are deliberately far above what a healthy call needs. The cloud one is
# shared with the adapter's fallback.
_SLOW_TIMEOUT = 1800

# Providers whose endpoint may be a slow model (local server or open gateway).
_SLOW_CAPABLE = frozenset({Provider.ollama, Provider.openai_compatible, Provider.openrouter})

# Ollama context window. Big enough to hold a real review prompt + diff + the
# emitted findings; ollama's own default (~4k) truncates the output to a stub.
# Sized to hold a real multi-file diff review — 16k was tight enough that a real
# review could overrun it and get truncated.
_OLLAMA_NUM_CTX = 32768


def default_timeout_for(provider: Provider) -> int:
    """The auto timeout (seconds) for a provider when none is given explicitly."""
    return _SLOW_TIMEOUT if provider in _SLOW_CAPABLE else CLOUD_TIMEOUT


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


# The values OpenRouter's own `reasoning.effort` field accepts. `default` — which
# litellm's normalised set includes — has no equivalent here, so it is reported
# as dropped rather than translated into a nearby level nobody asked for.
_OPENROUTER_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})


def dropped_params(provider: Provider, model: str, params: Iterable[str]) -> list[str]:
    """The names in *params* that litellm will silently discard for this model.

    The adapter runs with ``drop_params`` on so one unsupported param can't fail
    a whole review — the cost is that a param litellm's capability map doesn't
    list for a model vanishes with no warning at all, and a knob that was never
    connected is indistinguishable from a knob that didn't help.

    Keyed off litellm's own two maps rather than a per-param special case, so a
    param added to the config later is covered without touching this function.
    Only OpenAI-vocabulary params are judged: a provider-native option (ollama's
    ``num_ctx``/``think``) never appears in the capability map and is not
    litellm's to drop, so flagging it would only teach the reader to ignore the
    warning. A lookup failure reports nothing — this is instrumentation, and it
    must never be the reason a review doesn't run.
    """
    import litellm

    # litellm re-exports this but doesn't list it in __all__, so mypy rejects
    # litellm.OPENAI_CHAT_COMPLETION_PARAMS — import it from where it's defined.
    from litellm.constants import OPENAI_CHAT_COMPLETION_PARAMS

    try:
        supported = set(
            litellm.get_supported_openai_params(
                model=model, custom_llm_provider=_PREFIXES[provider]
            )
            or []
        )
    except Exception:  # pragma: no cover - defensive; litellm raises on odd ids
        return []
    return sorted(
        name for name in params if name in OPENAI_CHAT_COMPLETION_PARAMS and name not in supported
    )


def _honour_param_support(provider: Provider, model: str, opts: dict[str, Any]) -> None:
    """Say (once, up front) which configured params this model will discard, and
    re-route the one that has a native home on OpenRouter.

    The OpenRouter branch is a DELIBERATE, narrowly scoped exception to the
    "litellm normalises every provider to one `completion()` call" decision in
    CLAUDE.md — not licence for general per-provider plumbing. The reason it has
    to exist here: litellm's openrouter transformation only forwards
    ``reasoning_effort`` when its capability map already flags the model
    reasoning-capable, and the newest models are not in that map — which is
    exactly the set a reasoning budget gets configured for. OpenRouter itself
    accepts the budget as a top-level ``reasoning`` object regardless of model,
    so that is what gets sent, via ``extra_body``.

    Never both: OpenRouter answers a request carrying ``reasoning`` *and*
    ``reasoning_effort`` with ``400 Only one of "reasoning" and
    "reasoning_effort" may be provided``. So the object is only added on the
    branch where litellm has already been observed to drop the flat param, and
    the flat param is removed when it is.
    """
    dropped = dropped_params(provider, model, opts)
    if provider is Provider.openrouter and "reasoning_effort" in dropped:
        effort = opts.pop("reasoning_effort")
        if effort in _OPENROUTER_REASONING_EFFORTS:
            opts["extra_body"] = {**opts.get("extra_body", {}), "reasoning": {"effort": effort}}
            dropped.remove("reasoning_effort")
    if dropped:
        _log.warning(
            "configured params are not supported by this model and will be ignored",
            extra={
                "provider": provider.value,
                "model": litellm_model_string(provider, model),
                "ignored_params": dropped,
            },
        )


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

    # Before anything the factory itself adds (timeout, credentials, ollama's own
    # options), so what is judged is exactly what the USER configured — and it is
    # judged here because this is where the litellm model string the capability
    # map keys on comes into existence.
    _honour_param_support(provider, model, opts)

    opts["timeout"] = timeout if timeout is not None else default_timeout_for(provider)

    if api_key is not None:
        opts["api_key"] = api_key

    # Keyless Azure: an Azure AD bearer token instead of a static key.
    if azure_ad_token is not None:
        opts["azure_ad_token"] = azure_ad_token

    is_ollama = provider is Provider.ollama
    if is_ollama:
        opts["api_base"] = api_base or DEFAULT_OLLAMA_BASE
        # `think` is deliberately NOT sent. Ollama already decides it per model —
        # its chat route defaults thinking ON for a thinking-capable model when the
        # field is unset, and 400s outright if you ask a non-thinking model for it.
        # So sending nothing gets the right answer for both, where sending either
        # literal gets one of them wrong.
        #
        # This used to be pinned to False, because a thinking model routed its whole
        # answer into the reasoning channel and returned EMPTY content under
        # structured output, leaving JSON mode nothing to parse. Two things have
        # since made that the wrong trade. Ollama now separates the trace into
        # `message.thinking` and leaves the answer in `message.content`; and when a
        # backend does still come back empty under a schema, the adapter drops
        # `response_format` and re-sends (see LiteLLMProvider._call), remembering it
        # for the rest of the run. The cost of being wrong is one re-send; the cost
        # of pinning it False was every local reasoning model reviewing with its
        # reasoning switched off, which four measured runs say is the single
        # biggest lever on finding quality there is.
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
