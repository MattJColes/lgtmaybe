"""Boundary interfaces are structural protocols, not inheritance requirements."""

from __future__ import annotations

from lgtmaybe.core.ports import GitHubGateway, ProviderClient, ReviewEngine
from lgtmaybe.engine.engine import LLMReviewEngine
from lgtmaybe.github.rest_gateway import RestGitHubGateway
from lgtmaybe.providers.litellm_provider import LiteLLMProvider


def test_ports_are_protocols() -> None:
    for port in (ProviderClient, GitHubGateway, ReviewEngine):
        assert getattr(port, "_is_protocol", False)


def test_production_implementations_do_not_inherit_ports() -> None:
    assert ProviderClient not in LiteLLMProvider.__mro__
    assert GitHubGateway not in RestGitHubGateway.__mro__
    assert ReviewEngine not in LLMReviewEngine.__mro__
