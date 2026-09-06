"""Tests for the ollama path: api_base config and model prefix."""

from __future__ import annotations

from lgtmaybe.core.models import Provider
from lgtmaybe.providers.factory import build_provider


class TestOllamaPath:
    def test_ollama_routes_to_configured_api_base(self) -> None:
        api_base = "http://host.docker.internal:11434"
        provider = build_provider(Provider.ollama, "llama2", api_base=api_base)
        assert provider.default_opts.get("api_base") == api_base

    def test_ollama_model_string_has_ollama_prefix(self) -> None:
        provider = build_provider(Provider.ollama, "llama2")
        assert provider.model == "ollama_chat/llama2"

    def test_ollama_default_api_base_is_localhost(self) -> None:
        provider = build_provider(Provider.ollama, "llama2")
        assert "localhost" in (provider.default_opts.get("api_base") or "")

    def test_ollama_tailscale_host_api_base(self) -> None:
        tailscale_base = "http://100.64.0.1:11434"
        provider = build_provider(Provider.ollama, "llama2", api_base=tailscale_base)
        assert provider.default_opts.get("api_base") == tailscale_base
