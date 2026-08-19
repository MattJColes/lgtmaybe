"""Boundary interfaces are structural protocols, not inheritance requirements."""

from __future__ import annotations

import pytest

from lgtmaybe.core import ports
from lgtmaybe.core.ports import GitHubGateway, ProviderClient, ReviewEngine, ReviewGateway
from lgtmaybe.engine.engine import LLMReviewEngine
from lgtmaybe.github.rest_gateway import RestGitHubGateway
from lgtmaybe.providers.litellm_provider import LiteLLMProvider

# Every optional capability a gateway may declare, by name, so the conformance
# test below fails loudly when one is added without being wired up.
CAPABILITIES = [
    name
    for name in dir(ports)
    if name.startswith("Supports") and getattr(getattr(ports, name), "_is_protocol", False)
]


def test_ports_are_protocols() -> None:
    for port in (ProviderClient, ReviewGateway, ReviewEngine):
        assert getattr(port, "_is_protocol", False)


def test_production_implementations_do_not_inherit_ports() -> None:
    assert ProviderClient not in LiteLLMProvider.__mro__
    assert ReviewGateway not in RestGitHubGateway.__mro__
    assert ReviewEngine not in LLMReviewEngine.__mro__


def test_the_old_github_only_port_name_still_resolves() -> None:
    """Renamed when lgtmaybe grew past one forge; the alias keeps adapters importing."""
    assert GitHubGateway is ReviewGateway


class TestCapabilities:
    """The ``Supports*`` protocols are a checklist for forge adapter authors.

    They are only worth having if they describe methods that really exist, so
    the reference adapter is asserted against every one of them.
    """

    def test_there_is_at_least_one(self) -> None:
        assert CAPABILITIES

    @pytest.mark.parametrize("name", CAPABILITIES)
    def test_capabilities_are_runtime_checkable(self, name: str) -> None:
        """The CLI probes a gateway at run time, so presence must be testable."""
        assert getattr(ports, name)._is_runtime_protocol

    @pytest.mark.parametrize("name", CAPABILITIES)
    def test_the_github_adapter_satisfies_every_capability(self, name: str) -> None:
        """GitHub is the complete adapter — it is what the full surface is drawn from."""
        gateway = RestGitHubGateway.__new__(RestGitHubGateway)
        assert isinstance(gateway, getattr(ports, name))
