"""Prompt caching of the static system prompt (P1).

The static system prompt (shared header + lens section + worked example) is
re-sent on every concurrent lens call and again on reflection. On providers
that support explicit cache breakpoints (Anthropic direct, Bedrock Claude/Nova)
the adapter marks the system message with ``cache_control: {"type": "ephemeral"}``
so those calls read the prefix from cache instead of re-paying for it.

Everything here monkeypatches ``litellm.completion`` at the boundary — no
network. The contract under test:

- the marker is attached ONLY on cache-capable provider routes (``anthropic/``,
  ``bedrock/``) whose model litellm's capability map says supports caching;
- only the (static) system message is marked — the user message (diff, intent)
  stays outside the cached prefix;
- below the documented 1,024-token minimum cacheable block the request is sent
  unchanged (a smaller block would silently not cache);
- with ``prompt_cache`` off, or on any other provider (ollama, openai-compatible,
  …), the request is byte-for-byte what it was before this feature existed;
- cache_read / cache_creation token counts are mapped into ``ProviderResult``.
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
]

_UNCACHEABLE_MODELS = [
    "ollama/llama3",
    "openai/gpt-4o",  # OpenAI caches automatically server-side — no marker
    "openai/local-model",  # openai-compatible route
    "azure/gpt-4o",
    "openrouter/anthropic/claude-3.5-sonnet",
    "vertex_ai/gemini-1.5-pro",
    "zai/glm-4.6",
]


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


def _sent_messages(model: str, *, prompt_cache: bool, system: str = _BIG_SYSTEM) -> list[Any]:
    """Run one completion and return the messages litellm actually received."""
    with patch("litellm.completion", return_value=_fake_response()) as mock_completion:
        provider = LiteLLMProvider(model=model, prompt_cache=prompt_cache)
        provider.complete(_messages(system), model)
    return mock_completion.call_args.kwargs["messages"]


class TestCacheControlMarking:
    @pytest.mark.parametrize("model", _CACHEABLE_MODELS)
    def test_system_prompt_marked_cacheable_on_supported_models(self, model: str) -> None:
        sent = _sent_messages(model, prompt_cache=True)
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
        sent = _sent_messages(model, prompt_cache=True)
        user = sent[1]
        assert user["role"] == "user"
        # The user message (diff + intent — volatile) is untouched: a plain
        # string with no cache marker, so it never busts or joins the prefix.
        assert isinstance(user["content"], str)

    @pytest.mark.parametrize("model", _UNCACHEABLE_MODELS)
    def test_request_unchanged_on_providers_without_cache_control(self, model: str) -> None:
        sent = _sent_messages(model, prompt_cache=True)
        assert sent == _messages()

    def test_request_unchanged_when_prompt_cache_disabled(self) -> None:
        sent = _sent_messages(_CACHEABLE_MODELS[0], prompt_cache=False)
        assert sent == _messages()

    def test_request_unchanged_below_minimum_cacheable_tokens(self) -> None:
        """Anthropic silently ignores cache blocks under 1,024 tokens — sending
        the marker would be a no-op, so the adapter doesn't rewrite the message."""
        sent = _sent_messages(_CACHEABLE_MODELS[0], prompt_cache=True, system=_SMALL_SYSTEM)
        assert sent == _messages(_SMALL_SYSTEM)

    def test_request_unchanged_without_a_system_message(self) -> None:
        messages = [{"role": "user", "content": "hi"}]
        with patch("litellm.completion", return_value=_fake_response()) as mock_completion:
            provider = LiteLLMProvider(model=_CACHEABLE_MODELS[0], prompt_cache=True)
            provider.complete(list(messages), _CACHEABLE_MODELS[0])
        assert mock_completion.call_args.kwargs["messages"] == messages

    def test_capability_lookup_failure_is_a_safe_no_op(self) -> None:
        """An unknown model (or a litellm map lookup error) must never break the
        call — caching is an optimisation, so the request goes out unmarked."""
        with patch(
            "lgtmaybe.providers.litellm_provider.supports_prompt_caching",
            side_effect=RuntimeError("boom"),
        ):
            sent = _sent_messages(_CACHEABLE_MODELS[0], prompt_cache=True)
        assert sent == _messages()

    def test_original_messages_list_is_not_mutated(self) -> None:
        messages = _messages()
        with patch("litellm.completion", return_value=_fake_response()):
            provider = LiteLLMProvider(model=_CACHEABLE_MODELS[0], prompt_cache=True)
            provider.complete(messages, _CACHEABLE_MODELS[0])
        assert messages == _messages()


class TestCacheUsageMapping:
    def test_cache_token_counts_mapped_from_usage(self) -> None:
        response = _fake_response(
            cache_read_input_tokens=1200,
            cache_creation_input_tokens=345,
        )
        with patch("litellm.completion", return_value=response):
            provider = LiteLLMProvider(model=_CACHEABLE_MODELS[0], prompt_cache=True)
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

    def test_cache_token_counts_default_to_zero(self) -> None:
        with patch("litellm.completion", return_value=_fake_response()):
            provider = LiteLLMProvider()
            result = provider.complete(_messages(), "ollama/llama3")
        assert result.cache_read_tokens == 0
        assert result.cache_creation_tokens == 0


class TestFactoryThreading:
    def test_factory_passes_prompt_cache_to_the_provider(self) -> None:
        provider = build_provider(Provider.anthropic, "claude-sonnet-4", prompt_cache=True)
        assert provider.prompt_cache is True

    def test_factory_default_is_off_for_direct_construction(self) -> None:
        provider = build_provider(Provider.anthropic, "claude-sonnet-4")
        assert provider.prompt_cache is False

    def test_prompt_cache_never_reaches_litellm_kwargs(self) -> None:
        """`prompt_cache` steers the adapter; it must not leak into the
        completion call as an (unknown) litellm parameter."""
        provider = build_provider(Provider.anthropic, "claude-sonnet-4", prompt_cache=True)
        assert "prompt_cache" not in provider.default_opts
        with patch("litellm.completion", return_value=_fake_response()) as mock_completion:
            provider.complete(_messages(), "anthropic/claude-sonnet-4")
        assert "prompt_cache" not in mock_completion.call_args.kwargs


class TestProviderMatrix:
    """Every provider either applies the marker or safely no-ops — never errors."""

    @pytest.mark.parametrize("provider", list(Provider))
    def test_prompt_cache_on_is_safe_for_every_provider(self, provider: Provider) -> None:
        client = build_provider(
            provider, "some-model", api_base="http://localhost:1234", prompt_cache=True
        )
        with patch("litellm.completion", return_value=_fake_response()) as mock_completion:
            result = client.complete(_messages(), "some-model")
        assert result.text == "ok"
        sent = mock_completion.call_args.kwargs["messages"]
        # Whatever a provider does, the volatile user content is never marked.
        assert isinstance(sent[-1]["content"], str)
