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

# Providers whose endpoint may be ONE box that serves requests in turn.
#
# lgtmaybe issues its whole lens fan-out at once, so on a single-slot server
# (ollama's OLLAMA_NUM_PARALLEL is 1 by default; llama.cpp without `-np`) five
# of six requests sit in the server's queue. Their timeout clocks are already
# running: the budget is measured from the moment the request is SENT, not from
# the moment the server starts on it. Unscaled, the last call in a six-wide
# fan-out has to be served within the same 1800s as the first, and on a slow
# model it can blow its budget having never been looked at — which posts
# "results may be incomplete" for a lens that was never actually slow.
#
# So the default is multiplied by the fan-out width for these two, and only
# these two. A hosted endpoint (including openrouter, which is capacity rather
# than a box) serves concurrently and has no queue to cover, and scaling there
# would buy nothing but a longer wait before a genuinely stuck call is reported.
#
# This does not run unbounded: `max_review_seconds` (3600s by default) is a
# whole-review deadline that stops queued work regardless of what any single
# call's budget says, so the scaled number is a ceiling the run rarely reaches.
_MAY_QUEUE = frozenset({Provider.ollama, Provider.openai_compatible})

# Ollama context window. Big enough to hold a real review prompt + diff + the
# emitted findings; ollama's own default (~4k) truncates the output to a stub.
# Sized to hold a real multi-file diff review — 16k was tight enough that a real
# review could overrun it and get truncated.
_OLLAMA_NUM_CTX = 32768

# Routes that get a default output ceiling, and the ceiling itself.
#
# A model under structured output can fail to terminate: the response keeps
# decoding, and with no ceiling the only thing that ever stops it is the
# per-call timeout — deliberately generous, because a slow endpoint has to be
# given time to answer. That makes the timeout the wrong instrument for a
# runaway decode: a single lens spent 18 minutes of sustained GPU on a one-file
# diff, and a nine-lens review takes hours. A finite ceiling stops it in
# seconds, and the stop is *reported* — a truncation posts the incomplete
# notice, where a timeout posts the same notice half an hour later.
#
# This was ollama-only on the theory that openrouter and openai-compatible may
# equally be a hosted API with its own sane ceiling. Benchmarking disproved it.
# Behind `openai-compatible` a local model emitted 223,558 tokens on ONE lens
# call; worse, `openrouter` is not safer for being hosted — one model there
# returned a 393k-token response that parsed into 699 false positives on a diff
# with nothing wrong in it, and another turned a 70,344-token response into 323
# findings on a clean diff. Unbounded decode is a property of the model and the
# structured-output task, not of who is hosting it. So all three share one
# number, and only the first-party APIs (which have not shown the failure) are
# left on the model's own ceiling.
#
# Half of `_OLLAMA_NUM_CTX`: measured findings payloads are hundreds of tokens,
# so the rest is headroom for a thinking model (reasoning is drawn from this
# same budget). Derived from that window so the two numbers are related rather
# than arbitrary. Measured against the benchmark corpus, 16384 truncates 2.5% of
# the calls that parse today while stopping every runaway observed (all of them
# 70k+); the older, tighter 8192 truncated 7.8% — real findings paid for the
# fix. It is a FIXED ceiling: raising `num_ctx` for a big diff buys room for the
# prompt, not a longer answer, and `max_tokens` is the knob for that.
_CAPPED_BY_DEFAULT = frozenset({Provider.ollama, Provider.openai_compatible, Provider.openrouter})
_DEFAULT_MAX_TOKENS = _OLLAMA_NUM_CTX // 2


def resolve_max_tokens(provider: Provider, configured: int | None = None) -> int | None:
    """The output ceiling to send for one call, or None to send none at all.

    ``configured`` is the user's ``max_tokens``: a positive value always wins,
    ``0`` means "explicitly uncapped" (spelled the way the rest of the config
    spells an off switch — ``max_review_seconds: 0``, ``context_lines: 0``), and
    ``None`` means "unset", which is where the provider default applies.
    """
    if configured is not None:
        return configured or None
    return _DEFAULT_MAX_TOKENS if provider in _CAPPED_BY_DEFAULT else None


def default_timeout_for(provider: Provider, *, concurrency: int = 1) -> int:
    """The auto timeout (seconds) for a provider when none is given explicitly.

    ``concurrency`` is the fan-out width the run will use. For a provider that
    may be a single-slot local server (:data:`_MAY_QUEUE`) the default is
    multiplied by it, so a call that spends its wait in the server's queue is
    still given a full budget for the work itself. Everywhere else it is
    ignored — see the comment on :data:`_MAY_QUEUE`.
    """
    base = _SLOW_TIMEOUT if provider in _SLOW_CAPABLE else CLOUD_TIMEOUT
    if provider in _MAY_QUEUE:
        return base * max(1, concurrency)
    return base


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
    concurrency: int = 1,
    **extra_opts: Any,
) -> LiteLLMProvider:
    """Build a configured LiteLLMProvider for the given provider and model.

    ``timeout`` of ``None`` resolves to a provider-aware default
    (:func:`default_timeout_for`) — so ollama always gets a long timeout without
    the caller having to ask, scaled by ``concurrency`` when the endpoint may be
    a server that queues. An explicit value is honoured as-is: ``timeout: 600``
    means 600, at any fan-out width.
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

    opts["timeout"] = (
        timeout if timeout is not None else default_timeout_for(provider, concurrency=concurrency)
    )

    # Resolved here, not at the caller, so every path that builds a provider gets
    # the ceiling — and so the one value that must never be sent (0, the
    # uncapped escape hatch, which litellm would forward as "generate nothing")
    # is removed in the same place it is interpreted.
    ceiling = resolve_max_tokens(provider, opts.pop("max_tokens", None))
    if ceiling is not None:
        opts["max_tokens"] = ceiling

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

    # Answered here because this is where both halves live: `dropped_params`
    # needs the Provider enum and the RAW model name, and the adapter holds
    # neither — only the already-prefixed litellm string. Fails open by
    # construction: `dropped_params` returns [] both when the param is supported
    # and when the lookup fails, so only a map that positively omits it marks the
    # route incapable.
    effort_supported = "reasoning_effort" not in dropped_params(
        provider, model, ["reasoning_effort"]
    )

    return LiteLLMProvider(
        model=resolved_model,
        fallback_model=resolved_fallback,
        effort_override_supported=effort_supported,
        **opts,
    )
