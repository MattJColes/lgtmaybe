"""The forge seam: which code host a PR/MR URL belongs to, and how to auth it.

lgtmaybe posts reviews to more than one code host. Everything upstream of the
gateway (engine, prompts, diff parsing) is host-neutral, so the only thing that
has to know *which* host is the small locator + credential layer tested here.
"""

from __future__ import annotations

import pytest

from lgtmaybe.core.forge import Forge, PRLocator, parse_pr_url, token_env_var


class TestParsePRUrl:
    """A PR/MR URL resolves to (forge, host, repo, number)."""

    def test_parses_a_github_pull_request(self) -> None:
        assert parse_pr_url("https://github.com/org/repo/pull/42") == PRLocator(
            forge=Forge.github, host="github.com", repo="org/repo", number=42
        )

    def test_parses_a_gitlab_merge_request(self) -> None:
        assert parse_pr_url("https://gitlab.com/org/repo/-/merge_requests/7") == PRLocator(
            forge=Forge.gitlab, host="gitlab.com", repo="org/repo", number=7
        )

    def test_gitlab_project_paths_may_nest_in_subgroups(self) -> None:
        """GitLab groups nest arbitrarily; the ``/-/`` separator ends the path."""
        located = parse_pr_url("https://gitlab.example.com/grp/sub/proj/-/merge_requests/3")
        assert located == PRLocator(
            forge=Forge.gitlab, host="gitlab.example.com", repo="grp/sub/proj", number=3
        )

    def test_parses_a_gitea_pull_request(self) -> None:
        """Gitea mirrors GitHub's URL shape but pluralises the ``pulls`` segment."""
        assert parse_pr_url("https://gitea.example.com/org/repo/pulls/9") == PRLocator(
            forge=Forge.gitea, host="gitea.example.com", repo="org/repo", number=9
        )

    def test_rejects_a_url_that_is_not_a_pull_request(self) -> None:
        with pytest.raises(ValueError, match="Could not parse"):
            parse_pr_url("https://github.com/org/repo")

    def test_the_error_names_every_supported_url_shape(self) -> None:
        """The message has to be actionable on whichever host the user is on."""
        with pytest.raises(ValueError) as excinfo:
            parse_pr_url("nonsense")
        message = str(excinfo.value)
        assert "/pull/" in message
        assert "/merge_requests/" in message
        assert "/pulls/" in message


class TestTokenEnvVar:
    """Each forge reads its own conventional token variable."""

    @pytest.mark.parametrize(
        ("forge", "expected"),
        [
            (Forge.github, "GITHUB_TOKEN"),
            (Forge.gitlab, "GITLAB_TOKEN"),
            (Forge.gitea, "GITEA_TOKEN"),
        ],
    )
    def test_names_the_conventional_variable(self, forge: Forge, expected: str) -> None:
        assert token_env_var(forge) == expected

    def test_every_forge_has_one(self) -> None:
        """A forge with no token variable could never authenticate."""
        assert {token_env_var(forge) for forge in Forge} == {
            "GITHUB_TOKEN",
            "GITLAB_TOKEN",
            "GITEA_TOKEN",
        }


class TestGatewayRegistry:
    """Which forges lgtmaybe can actually build a gateway for."""

    @pytest.mark.parametrize("forge", list(Forge))
    def test_every_forge_the_parser_knows_can_be_built(self, forge: Forge) -> None:
        """A URL lgtmaybe parses but cannot act on would be a dead end."""
        from lgtmaybe.cli import gateway_builder

        assert gateway_builder(forge) is not None

    def test_the_gitlab_builder_carries_the_nested_project_path(self) -> None:
        """GitLab groups nest, and the whole path addresses the project."""
        from lgtmaybe.cli import gateway_builder
        from lgtmaybe.core.models import ReviewConfig

        build = gateway_builder(Forge.gitlab)
        assert build is not None
        located = parse_pr_url("https://gl.internal/grp/sub/proj/-/merge_requests/9")
        gateway = build(located, "token", ReviewConfig(provider="ollama", model="llama3"))
        assert gateway._project == "grp%2Fsub%2Fproj"
        assert "gl.internal" in gateway._api

    def test_the_gitea_builder_carries_the_host_through(self) -> None:
        """Self-hosted is the norm on Gitea, so the API base cannot be a constant."""
        from lgtmaybe.cli import gateway_builder
        from lgtmaybe.core.models import ReviewConfig

        build = gateway_builder(Forge.gitea)
        assert build is not None
        located = parse_pr_url("https://git.acme.internal/team/svc/pulls/12")
        gateway = build(located, "token", ReviewConfig(provider="ollama", model="llama3"))
        assert "git.acme.internal" in gateway._api
        assert gateway._pr_number == 12
