"""Prompt caching of the shared prefix.

The system preamble and wrapped diff are re-sent on every concurrent lens call
and again on reflection. On routes that take an explicit cache breakpoint the
adapter marks that prefix with ``cache_control: {"type": "ephemeral"}`` so those
calls read it from cache instead of re-paying for it; on routes that cache
automatically the identical prefix shape does the same job unmarked.

Everything here monkeypatches ``litellm.completion`` at the boundary — no
network. The contract under test:

- the marker is attached ONLY on routes litellm forwards it on (``anthropic/``,
  ``bedrock/``, ``vertex_ai/``, ``zai/``, ``openrouter/``) whose model litellm's
  capability map says supports caching;
- only the shared prefix is marked — the per-lens block stays outside it;
- below the lowest documented minimum cacheable block (1,024 tokens) the request
  is sent unchanged, since no model can cache a prefix that small;
- on a route with neither mechanism (ollama, openai-compatible), split user
  blocks are merged into one plain message;
- a sticky ``prompt_cache_key`` derived from the prefix pins the fan-out to one
  provider endpoint;
- cache read / write token counts are mapped into ``ProviderResult`` under every
  field name the providers use for them.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from lgtmaybe.core.models import Provider
from lgtmaybe.providers.factory import build_provider
from lgtmaybe.providers.litellm_provider import LiteLLMProvider

# A system prompt comfortably above the 1,024-token minimum cacheable block
# (~6000 tokens at 4 chars/token).
_BIG_SYSTEM = "You are an expert code reviewer. " + ("Review the diff carefully. " * 900)
_SMALL_SYSTEM = "You are an expert code reviewer."

_CACHEABLE_MODELS = [
    "anthropic/claude-sonnet-4-20250514",
    "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
    "bedrock/us.amazon.nova-pro-v1:0",
    # Vertex: litellm documents cache_control for BOTH the Claude partner
    # models (prompt-caching beta) and Gemini (translated to Google's
    # cachedContents API).
    "vertex_ai/claude-sonnet-4-5",
    "vertex_ai/gemini-2.5-pro",
    # GLM / Zhipu: ZAIChatConfig overrides the strip step to always preserve
    # cache_control, so the breakpoint reaches the API.
    "zai/glm-4.6",
    # OpenRouter passes cache_control through for the model families that take
    # it (claude, gemini, minimax, glm, z-ai) and STRIPS it for the rest, so we
    # mark broadly and let litellm decide per model — see the deepseek case in
    # TestOpenRouterBreakpoints.
    "openrouter/anthropic/claude-sonnet-4.5",
    "openrouter/z-ai/glm-4.6",
    "openrouter/minimax/minimax-m2",
]

_UNCACHEABLE_MODELS = [
    "ollama/llama3",
    "openai/gpt-4o",  # OpenAI caches automatically server-side — no marker
    "openai/local-model",  # openai-compatible route
    "azure/gpt-4o",
]


def _block(text: str, *, cached: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if cached:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def _fake_response(content: str = "ok", **usage_extra: Any) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, **usage_extra),
    )


def _messages(system: str = _BIG_SYSTEM) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "diff --git a/x b/x\n+dynamic content"},
    ]


def test_removed_prompt_cache_option_is_rejected_at_construction() -> None:
    with pytest.raises(TypeError, match="prompt_cache.*removed"):
        LiteLLMProvider(prompt_cache=False)


def _sent_messages(model: str, *, system: str = _BIG_SYSTEM) -> list[Any]:
    """Run one completion and return the messages litellm actually received."""
    with patch("litellm.completion", return_value=_fake_response()) as mock_completion:
        provider = LiteLLMProvider(model=model)
        provider.complete(_messages(system), model)
    return mock_completion.call_args.kwargs["messages"]


class TestCacheControlMarking:
    @pytest.mark.parametrize("model", _CACHEABLE_MODELS)
    def test_system_prompt_marked_cacheable_on_supported_models(self, model: str) -> None:
        sent = _sent_messages(model)
        system = sent[0]
        assert system["role"] == "system"
        # Content becomes a single text block carrying the cache breakpoint.
        assert isinstance(system["content"], list)
        [block] = system["content"]
        assert block["type"] == "text"
        assert block["text"] == _BIG_SYSTEM
        assert block["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.parametrize("model", _CACHEABLE_MODELS)
    def test_dynamic_user_content_stays_outside_the_cached_region(self, model: str) -> None:
        sent = _sent_messages(model)
        user = sent[1]
        assert user["role"] == "user"
        # The user message (diff + intent — volatile) is untouched: a plain
        # string with no cache marker, so it never busts or joins the prefix.
        assert isinstance(user["content"], str)

    @pytest.mark.parametrize("model", _UNCACHEABLE_MODELS)
    def test_request_unchanged_on_providers_without_cache_control(self, model: str) -> None:
        sent = _sent_messages(model)
        assert sent == _messages()

    def test_request_unchanged_below_minimum_cacheable_tokens(self) -> None:
        """Anthropic silently ignores cache blocks under 1,024 tokens — sending
        the marker would be a no-op, so the adapter doesn't rewrite the message."""
        sent = _sent_messages(_CACHEABLE_MODELS[0], system=_SMALL_SYSTEM)
        assert sent == _messages(_SMALL_SYSTEM)

    def test_request_unchanged_without_a_system_message(self) -> None:
        messages = [{"role": "user", "content": "hi"}]
        with patch("litellm.completion", return_value=_fake_response()) as mock_completion:
            provider = LiteLLMProvider(model=_CACHEABLE_MODELS[0])
            provider.complete(list(messages), _CACHEABLE_MODELS[0])
        assert mock_completion.call_args.kwargs["messages"] == messages

    def test_capability_lookup_failure_is_a_safe_no_op(self) -> None:
        """An unknown model (or a litellm map lookup error) must never break the
        call — caching is an optimisation, so the request goes out unmarked."""
        with patch(
            "lgtmaybe.providers.litellm_provider.supports_prompt_caching",
            side_effect=RuntimeError("boom"),
        ):
            sent = _sent_messages(_CACHEABLE_MODELS[0])
        assert sent == _messages()

    def test_original_messages_list_is_not_mutated(self) -> None:
        messages = _messages()
        with patch("litellm.completion", return_value=_fake_response()):
            provider = LiteLLMProvider(model=_CACHEABLE_MODELS[0])
            provider.complete(messages, _CACHEABLE_MODELS[0])
        assert messages == _messages()


_DIFF_PREFIX = "diff --git a/x b/x\n" + ("+some changed line of code\n" * 700)
_LENS_BLOCK = "Review the diff above through the security lens only."


def _split_messages(system: str = _BIG_SYSTEM, prefix: str = _DIFF_PREFIX) -> list[dict[str, str]]:
    """The engine's split (cache-shaped) layout: preamble, shared prefix, lens block."""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": prefix},
        {"role": "user", "content": _LENS_BLOCK},
    ]


def _sent_split(model: str, *, system: str = _BIG_SYSTEM) -> list[Any]:
    with patch("litellm.completion", return_value=_fake_response()) as mock_completion:
        provider = LiteLLMProvider(model=model)
        provider.complete(_split_messages(system), model)
    return mock_completion.call_args.kwargs["messages"]


class TestSplitShapeCacheMarking:
    """The engine's split prompt shape: shared preamble + diff prefix + lens block.

    On breakpoint routes the diff prefix must join the cached region (this is
    the whole point — the diff is identical across every lens call in a batch);
    on all other routes the user messages merge back into the single plain
    message those providers have always received.
    """

    @pytest.mark.parametrize("model", _CACHEABLE_MODELS)
    def test_prefix_block_carries_the_breakpoint_and_lens_block_does_not(self, model: str) -> None:
        sent = _sent_split(model)
        assert len(sent) == 2  # system + ONE merged user message
        [sys_block] = sent[0]["content"]
        assert sys_block["cache_control"] == {"type": "ephemeral"}
        prefix_block, lens_block = sent[1]["content"]
        assert prefix_block["text"] == _DIFF_PREFIX
        assert prefix_block["cache_control"] == {"type": "ephemeral"}
        assert lens_block["text"] == _LENS_BLOCK
        assert "cache_control" not in lens_block

    def test_small_system_still_caches_once_the_diff_crosses_the_minimum(self) -> None:
        """The 1,024-token minimum applies to the cumulative prefix, not per
        block: a small preamble plus a big diff still earns the diff breakpoint."""
        sent = _sent_split(_CACHEABLE_MODELS[0], system=_SMALL_SYSTEM)
        # System alone is under the minimum → left as a plain string.
        assert sent[0]["content"] == _SMALL_SYSTEM
        prefix_block, lens_block = sent[1]["content"]
        assert prefix_block["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in lens_block

    @pytest.mark.parametrize("model", _UNCACHEABLE_MODELS)
    def test_split_shape_merges_to_one_plain_user_message_elsewhere(self, model: str) -> None:
        sent = _sent_split(model)
        assert len(sent) == 2
        assert sent[0] == {"role": "system", "content": _BIG_SYSTEM}
        assert sent[1] == {"role": "user", "content": f"{_DIFF_PREFIX}\n\n{_LENS_BLOCK}"}


class TestOpenRouterBreakpoints:
    """OpenRouter is marked broadly on purpose — litellm gates it per model.

    ``OpenrouterConfig`` preserves ``cache_control`` for the model families that
    accept it (claude, gemini, minimax, glm, z-ai) and removes it for every
    other model before the request goes out. So the adapter does not need its
    own per-model allowlist for this route: marking a deepseek or qwen model is
    a no-op, not an error, and those backends cache the shared prefix
    automatically anyway.

    These assertions run litellm's real transform (no network) so a litellm
    upgrade that changes the supported-family list fails here rather than
    silently costing money on every review.
    """

    @staticmethod
    def _breakpoints_reaching_the_api(model: str) -> int:
        import copy
        import json

        from litellm.llms.openrouter.chat.transformation import OpenrouterConfig

        messages = [
            {"role": "system", "content": [_block("PREAMBLE", cached=True)]},
            {"role": "user", "content": [_block("DIFF", cached=True), _block("LENS")]},
        ]
        out = OpenrouterConfig().transform_request(
            model=model,
            messages=copy.deepcopy(messages),
            optional_params={},
            litellm_params={"custom_llm_provider": "openrouter"},
            headers={},
        )
        return json.dumps(out["messages"]).count("cache_control")

    @pytest.mark.parametrize(
        "model",
        ["anthropic/claude-sonnet-4.5", "z-ai/glm-4.6", "minimax/minimax-m2"],
    )
    def test_breakpoints_survive_for_supported_families(self, model: str) -> None:
        assert self._breakpoints_reaching_the_api(model) == 2

    @pytest.mark.parametrize("model", ["deepseek/deepseek-chat", "qwen/qwen3-max"])
    def test_breakpoints_are_stripped_for_the_rest_not_rejected(self, model: str) -> None:
        """The reason we can mark the whole openrouter route: an unsupported
        model loses the marker upstream instead of erroring on it."""
        assert self._breakpoints_reaching_the_api(model) == 0

    def test_adapter_marks_a_stripped_model_anyway(self) -> None:
        """deepseek via openrouter still gets our breakpoint — harmless (litellm
        removes it) and it keeps the split prefix identical across lenses, which
        is what deepseek's automatic caching keys on."""
        sent = _sent_split("openrouter/deepseek/deepseek-chat")
        assert len(sent) == 2
        assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


class TestCacheUsageMapping:
    def test_cache_token_counts_mapped_from_usage(self) -> None:
        response = _fake_response(
            cache_read_input_tokens=1200,
            cache_creation_input_tokens=345,
        )
        with patch("litellm.completion", return_value=response):
            provider = LiteLLMProvider(model=_CACHEABLE_MODELS[0])
            result = provider.complete(_messages(), _CACHEABLE_MODELS[0])
        assert result.cache_read_tokens == 1200
        assert result.cache_creation_tokens == 345

    def test_cached_tokens_fall_back_to_prompt_tokens_details(self) -> None:
        """OpenAI-style responses report cache reads under
        usage.prompt_tokens_details.cached_tokens — map those too."""
        response = _fake_response(
            prompt_tokens_details=SimpleNamespace(cached_tokens=800),
        )
        with patch("litellm.completion", return_value=response):
            provider = LiteLLMProvider()
            result = provider.complete(_messages(), "openai/gpt-4o")
        assert result.cache_read_tokens == 800
        assert result.cache_creation_tokens == 0

    def test_cache_creation_falls_back_to_prompt_tokens_details_too(self) -> None:
        """litellm normalises cache creation into the same wrapper it uses for
        reads. Reads had a fallback and creation didn't, so any route reporting
        only through the wrapper showed 0 cache writes — indistinguishable in
        --profile from caching not being applied at all."""
        response = _fake_response(
            prompt_tokens_details=SimpleNamespace(cached_tokens=800, cache_creation_tokens=64),
        )
        with patch("litellm.completion", return_value=response):
            provider = LiteLLMProvider()
            result = provider.complete(_messages(), "openrouter/anthropic/claude-sonnet-4.5")
        assert result.cache_read_tokens == 800
        assert result.cache_creation_tokens == 64

    def test_cache_writes_map_from_openrouters_field_name(self) -> None:
        """OpenRouter reports writes as prompt_tokens_details.cache_write_tokens,
        not litellm's cache_creation_tokens — and litellm passes the raw field
        through. Missing it reported 0 writes on every openrouter review, which
        reads as 'caching never engaged'."""
        response = _fake_response(
            prompt_tokens_details=SimpleNamespace(cached_tokens=10318, cache_write_tokens=512),
        )
        with patch("litellm.completion", return_value=response):
            provider = LiteLLMProvider()
            result = provider.complete(_messages(), "openrouter/anthropic/claude-sonnet-4.5")
        assert result.cache_read_tokens == 10318
        assert result.cache_creation_tokens == 512

    def test_cache_token_counts_default_to_zero(self) -> None:
        with patch("litellm.completion", return_value=_fake_response()):
            provider = LiteLLMProvider()
            result = provider.complete(_messages(), "ollama/llama3")
        assert result.cache_read_tokens == 0
        assert result.cache_creation_tokens == 0


class TestProviderMatrix:
    """Every provider either applies the marker or safely no-ops — never errors."""

    @pytest.mark.parametrize("provider", list(Provider))
    def test_prompt_caching_is_safe_for_every_provider(self, provider: Provider) -> None:
        client = build_provider(provider, "some-model", api_base="http://localhost:1234")
        with patch("litellm.completion", return_value=_fake_response()) as mock_completion:
            result = client.complete(_messages(), "some-model")
        assert result.text == "ok"
        sent = mock_completion.call_args.kwargs["messages"]
        # Whatever a provider does, the volatile user content is never marked.
        assert isinstance(sent[-1]["content"], str)


def test_capability_lookup_memoized_per_model() -> None:
    """The litellm capability map is consulted once per model, not per call.

    A review fans out many completions on the same model string; the
    supports-caching answer is a pure function of that string, so the
    adapter memoizes it on the instance.
    """
    model = _CACHEABLE_MODELS[0]
    with (
        patch("litellm.completion", return_value=_fake_response()),
        patch(
            "lgtmaybe.providers.litellm_provider.supports_prompt_caching",
            return_value=True,
        ) as lookup,
    ):
        provider = LiteLLMProvider(model=model)
        for _ in range(3):
            provider.complete(_messages(), model)
    assert lookup.call_count == 1


class TestStickyCacheKey:
    """OpenRouter pins a conversation to one provider endpoint to keep its cache
    warm, but without a key it only starts doing so *after* it observes a cache
    hit — too late for a concurrent lens fan-out. It accepts `prompt_cache_key`
    as that key, and OpenAI uses the same field as a cache-routing hint, so one
    param serves both. Derived from the cacheable prefix itself: identical
    across every lens of a batch, different for another PR or the reflection
    pass, and no plumbing through the engine.
    """

    @staticmethod
    def _sent_kwargs(model: str) -> dict[str, Any]:
        with patch("litellm.completion", return_value=_fake_response()) as mock_completion:
            provider = LiteLLMProvider(model=model)
            provider.complete(_split_messages(), model)
        return dict(mock_completion.call_args.kwargs)

    def test_key_is_sent_and_is_stable_for_the_same_prefix(self) -> None:
        model = "openrouter/anthropic/claude-sonnet-4.5"
        first = self._sent_kwargs(model)["prompt_cache_key"]
        second = self._sent_kwargs(model)["prompt_cache_key"]
        assert first and first == second
        assert len(first) <= 256  # OpenRouter's documented ceiling

    def test_a_different_prefix_gets_a_different_key(self) -> None:
        model = "openrouter/anthropic/claude-sonnet-4.5"
        with patch("litellm.completion", return_value=_fake_response()) as mock:
            provider = LiteLLMProvider(model=model)
            provider.complete(_split_messages(), model)
            provider.complete(_split_messages(prefix=_DIFF_PREFIX + "\n+another"), model)
        keys = [c.kwargs["prompt_cache_key"] for c in mock.call_args_list]
        assert keys[0] != keys[1]

    def test_lens_block_does_not_change_the_key(self) -> None:
        """Every lens of a batch must share the key — the lens block is the one
        part of the message that differs, and it sits outside the cached prefix."""
        model = "openrouter/anthropic/claude-sonnet-4.5"
        messages = _split_messages()
        other = [*messages[:-1], {"role": "user", "content": "A totally different lens block."}]
        with patch("litellm.completion", return_value=_fake_response()) as mock:
            provider = LiteLLMProvider(model=model)
            provider.complete(messages, model)
            provider.complete(other, model)
        keys = [c.kwargs["prompt_cache_key"] for c in mock.call_args_list]
        assert keys[0] == keys[1]

    def test_caller_supplied_key_wins(self) -> None:
        model = "openrouter/anthropic/claude-sonnet-4.5"
        with (
            patch("litellm.completion", return_value=_fake_response()) as mock,
            patch("lgtmaybe.providers.litellm_provider._prefix_cache_key") as derive,
        ):
            provider = LiteLLMProvider(model=model)
            provider.complete(_split_messages(), model, prompt_cache_key="mine")
        assert mock.call_args.kwargs["prompt_cache_key"] == "mine"
        derive.assert_not_called()
