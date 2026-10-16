"""Boundary interfaces are structural protocols, not inheritance requirements."""

from __future__ import annotations

import pytest

from lgtmaybe.azure_devops import AzureDevOpsGateway
from lgtmaybe.core import ports
from lgtmaybe.core.ports import GitHubGateway, ProviderClient, ReviewEngine, ReviewGateway
from lgtmaybe.engine.engine import LLMReviewEngine
from lgtmaybe.gitea import GiteaGateway
from lgtmaybe.github.rest_gateway import RestGitHubGateway
from lgtmaybe.gitlab import GitLabGateway
from lgtmaybe.providers.litellm_provider import LiteLLMProvider
from tests.fakes import RecoveringFakeProvider

# Optional capabilities the ENGINE probes on the provider adapter, by name.
# One protocol: the adapter-side halves of the engine's recovery steps, kept
# together so the engine's whole dependency on the adapter is in one place.
# Kept explicit so a new protocol added to either side without a conformance
# test fails loudly against the other.
PROVIDER_CAPABILITIES = ("SupportsRecovery",)

# Every optional capability a gateway may declare, by name, so the conformance
# test below fails loudly when one is added without being wired up.
CAPABILITIES = [
    name
    for name in dir(ports)
    if name.startswith("Supports")
    and getattr(getattr(ports, name), "_is_protocol", False)
    and name not in PROVIDER_CAPABILITIES
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


def test_scan_manifests_is_its_own_capability() -> None:
    """Gitea and Azure DevOps serve manifest text without incremental review.

    Bundling ``set_scan_manifests`` into ``SupportsIncremental`` told adapter
    authors the group was all-or-nothing when it was not.
    """
    assert not hasattr(ports.SupportsIncremental, "set_scan_manifests")
    assert hasattr(ports.SupportsScanManifests, "set_scan_manifests")


class TestProviderCapabilities:
    """The provider-side ``Supports*`` protocols the engine probes with isinstance.

    A capability renamed on the adapter used to vanish silently behind a
    ``getattr`` probe and switch its recovery step off; these assertions are
    what make the rename loud instead.
    """

    def test_every_protocol_is_claimed_by_exactly_one_side(self) -> None:
        """A new protocol forgotten here fails against the other adapter below.

        A name claimed by both sides would silently drop out of the gateway
        conformance checklist, so exclusivity is asserted, not just presence.
        """
        assert all(hasattr(ports, name) for name in PROVIDER_CAPABILITIES)
        assert not (set(PROVIDER_CAPABILITIES) & set(CAPABILITIES))

    @pytest.mark.parametrize("name", PROVIDER_CAPABILITIES)
    def test_capabilities_are_runtime_checkable(self, name: str) -> None:
        assert getattr(ports, name)._is_runtime_protocol

    @pytest.mark.parametrize("name", PROVIDER_CAPABILITIES)
    def test_the_litellm_adapter_satisfies_every_capability(self, name: str) -> None:
        provider = LiteLLMProvider()
        assert isinstance(provider, getattr(ports, name)), name

    def test_the_recovering_fake_satisfies_the_recovery_protocol(self) -> None:
        """Fake-based engine tests take their recovery steps through this fake.
        A member renamed or dropped here would silently switch those steps off
        across the suite, so it is held to the protocol like the adapter."""
        assert isinstance(RecoveringFakeProvider(), ports.SupportsRecovery)

    def test_the_recovery_protocol_names_the_primary_model(self) -> None:
        """The engine discloses a second answering model by comparing it with
        the adapter's primary, read through the protocol rather than a bare
        ``hasattr``, so a rename here must be loud."""
        assert hasattr(ports.SupportsRecovery, "model")
        assert LiteLLMProvider(model="openai/gpt-4o").model == "openai/gpt-4o"

    def test_what_a_call_lost_is_not_a_capability(self) -> None:
        """Schema and temperature drops ride each result, never the adapter, so
        no protocol may ask the adapter for a "dropped this run" report."""
        for name in PROVIDER_CAPABILITIES:
            protocol = getattr(ports, name)
            assert not hasattr(protocol, "schema_dropped")
            assert not hasattr(protocol, "temperature_dropped")


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


# What each shipped gateway is used for, capability by capability. Exact, not a
# lower bound: a gateway that loses one method of a group (a rename) loses the
# whole feature, and one that grows a group by accident is suddenly probed for
# it, so drift either way fails here. Mirrors the table in
# docs/explanation/architecture.md.
_EVERY_FORGE = {
    "SupportsFileContents",
    "SupportsDescribe",
    "SupportsDiagram",
    "SupportsConversation",
    "SupportsScanManifests",
    "SupportsCompletionMark",
}
SHIPPED_GATEWAYS: dict[type, set[str]] = {
    RestGitHubGateway: set(CAPABILITIES),
    GitLabGateway: _EVERY_FORGE
    | {
        "SupportsIncremental",
        "SupportsThreadResolution",
        "SupportsLabels",
        "SupportsChecks",
        "SupportsFeedback",
    },
    GiteaGateway: _EVERY_FORGE | {"SupportsLabels", "SupportsChecks", "SupportsPermissions"},
    AzureDevOpsGateway: _EVERY_FORGE,
}


class TestShippedGatewayCapabilities:
    """Each shipped gateway satisfies exactly the capability groups it is used for."""

    @pytest.mark.parametrize("gateway_type", SHIPPED_GATEWAYS, ids=lambda t: t.__name__)
    def test_claims_exactly_its_capabilities(self, gateway_type: type) -> None:
        gateway = gateway_type.__new__(gateway_type)
        claimed = {name for name in CAPABILITIES if isinstance(gateway, getattr(ports, name))}
        assert claimed == SHIPPED_GATEWAYS[gateway_type]

    def test_completion_mark_is_its_own_capability(self) -> None:
        """Gitea and Azure DevOps stamp completion as their resolve-on-fix gate
        without offering incremental review, so the stamp is its own group."""
        assert hasattr(ports.SupportsCompletionMark, "mark_reviewed")
        # An incremental review still needs somewhere to stamp its watermark.
        assert issubclass(ports.SupportsIncremental, ports.SupportsCompletionMark)

    def test_the_diff_fingerprint_pair_has_a_home(self) -> None:
        """The CLI reads and writes it, so a rename must be loud, not a skipped no-op."""
        assert hasattr(ports.SupportsDiffFingerprint, "last_reviewed_diff")
        assert hasattr(ports.SupportsDiffFingerprint, "mark_reviewed_diff")
        # It only means anything beside the watermark it fingerprints.
        assert issubclass(ports.SupportsDiffFingerprint, ports.SupportsIncremental)
