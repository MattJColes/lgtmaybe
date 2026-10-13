"""The forge seam: which code host a PR/MR URL belongs to, and how to auth it.

lgtmaybe posts reviews to more than one code host. Everything upstream of the
gateway (engine, prompts, diff parsing) is host-neutral, so the only thing that
has to know *which* host is the small locator + credential layer tested here.
"""

from __future__ import annotations

import pytest

from lgtmaybe.core.forge import Forge, PRLocator, parse_pr_url, token_env_var, token_env_vars


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

    def test_parses_an_azure_devops_pull_request(self) -> None:
        """Organisation, project, and repository all travel in ``repo``."""
        located = parse_pr_url("https://dev.azure.com/acme/Payments/_git/api/pullrequest/42")
        assert located == PRLocator(
            forge=Forge.azure_devops, host="dev.azure.com", repo="acme/Payments/api", number=42
        )

    def test_parses_a_legacy_visualstudio_pull_request(self) -> None:
        """The legacy host carries the organisation in its subdomain."""
        located = parse_pr_url("https://acme.visualstudio.com/Payments/_git/api/pullrequest/7")
        assert located == PRLocator(
            forge=Forge.azure_devops,
            host="acme.visualstudio.com",
            repo="acme/Payments/api",
            number=7,
        )

    def test_azure_devops_segments_are_decoded(self) -> None:
        """Project names may contain spaces; the locator carries them readable."""
        located = parse_pr_url(
            "https://dev.azure.com/acme/My%20Project/_git/my%20repo/pullrequest/3?_a=files"
        )
        assert located.repo == "acme/My Project/my repo"
        assert located.number == 3

    def test_azure_devops_short_form_names_the_project_after_the_repo(self) -> None:
        """Azure DevOps drops the project segment when it equals the repo name."""
        located = parse_pr_url("https://dev.azure.com/acme/_git/api/pullrequest/5")
        assert located.repo == "acme/api/api"

    @pytest.mark.parametrize(
        "url",
        [
            "https://dev.azure.com/acme/Payments/_git/api",
            "https://dev.azure.com/acme/Payments/_git/api/pullrequest/abc",
            "https://dev.azure.com/acme/Payments/_git/api/pullrequests",
            "https://dev.azure.com/_git/api/pullrequest/1",
            "https://dev.azure.com/acme/%2E%2E/_git/api/pullrequest/1",
            "https://dev.azure.com/acme/a/b/_git/api/pullrequest/1",
            "https://dev.azure.com/acme/Payments/_git/a%2Fb/pullrequest/1",
        ],
    )
    def test_rejects_malformed_azure_devops_urls(self, url: str) -> None:
        with pytest.raises(ValueError):
            parse_pr_url(url)

    def test_azure_devops_server_is_named_as_unsupported(self) -> None:
        """An on-premises collection is recognisable, so say why it is refused."""
        with pytest.raises(ValueError, match="Azure DevOps Server"):
            parse_pr_url(
                "https://tfs.corp.example/tfs/DefaultCollection/Proj/_git/api/pullrequest/1"
            )

    def test_rejects_a_url_that_is_not_a_pull_request(self) -> None:
        with pytest.raises(ValueError, match="Could not parse"):
            parse_pr_url("https://github.com/org/repo")

    @pytest.mark.parametrize(
        "url",
        [
            "https://ghe.acme.io/acme/api/pull/12",
            "https://github.com.evil.example/acme/api/pull/12",
            "https://token@github.com/acme/api/pull/12",
        ],
    )
    def test_refuses_a_github_url_on_any_other_host(self, url: str) -> None:
        """The GitHub gateway always calls api.github.com with GITHUB_TOKEN.

        Accepting another host's URL would send that token to github.com for a
        repo the user never named — refuse it, as the Action path refuses GHES.
        """
        with pytest.raises(ValueError, match="GitHub Enterprise Server is not supported"):
            parse_pr_url(url)

    @pytest.mark.parametrize("host", ["github.com", "www.github.com", "GitHub.com"])
    def test_accepts_github_dot_com(self, host: str) -> None:
        located = parse_pr_url(f"https://{host}/org/repo/pull/42")
        assert (located.forge, located.repo, located.number) == (Forge.github, "org/repo", 42)

    def test_accepts_a_trailing_tab_segment(self) -> None:
        """A URL copied from the Files tab still names the same PR."""
        located = parse_pr_url("https://github.com/org/repo/pull/42/files")
        assert (located.repo, located.number) == ("org/repo", 42)

    def test_the_host_is_the_url_host_not_a_path_segment(self) -> None:
        """A Gitea served under a sub-path must not turn that segment into the host."""
        with pytest.raises(ValueError, match="Could not parse"):
            parse_pr_url("https://example.com/gitea/org/repo/pulls/1")

    def test_the_error_names_every_supported_url_shape(self) -> None:
        """The message has to be actionable on whichever host the user is on."""
        with pytest.raises(ValueError) as excinfo:
            parse_pr_url("nonsense")
        message = str(excinfo.value)
        assert "/pull/" in message
        assert "/merge_requests/" in message
        assert "/pulls/" in message
        assert "/pullrequest/" in message


class TestTokenEnvVar:
    """Each forge reads its own conventional token variable."""

    @pytest.mark.parametrize(
        ("forge", "expected"),
        [
            (Forge.github, "GITHUB_TOKEN"),
            (Forge.gitlab, "GITLAB_TOKEN"),
            (Forge.gitea, "GITEA_TOKEN"),
            (Forge.azure_devops, "AZURE_DEVOPS_TOKEN"),
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
            "AZURE_DEVOPS_TOKEN",
        }

    def test_azure_devops_also_accepts_the_pipeline_access_token(self) -> None:
        """A PAT wins when both are set; the pipeline's own token is the fallback."""
        assert token_env_vars(Forge.azure_devops) == ("AZURE_DEVOPS_TOKEN", "SYSTEM_ACCESSTOKEN")

    def test_other_forges_read_exactly_one_variable(self) -> None:
        for forge in (Forge.github, Forge.gitlab, Forge.gitea):
            assert token_env_vars(forge) == (token_env_var(forge),)


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

    def test_the_gitea_builder_preserves_an_http_origin(self) -> None:
        from lgtmaybe.cli import gateway_builder
        from lgtmaybe.core.models import ReviewConfig

        located = parse_pr_url("http://git.acme.internal:3000/team/svc/pulls/12")
        build = gateway_builder(located.forge)
        assert build is not None

        gateway = build(
            located,
            "token",
            ReviewConfig(provider="ollama", model="llama3"),
        )

        assert gateway._api.startswith("http://git.acme.internal:3000/")

    def test_the_azure_devops_builder_addresses_the_organisation(self) -> None:
        from lgtmaybe.cli import gateway_builder
        from lgtmaybe.core.models import ReviewConfig

        located = parse_pr_url("https://dev.azure.com/acme/My%20Project/_git/api/pullrequest/4")
        build = gateway_builder(located.forge)
        assert build is not None
        gateway = build(located, "token", ReviewConfig(provider="ollama", model="llama3"))
        assert gateway._repo_api == (
            "https://dev.azure.com/acme/My%20Project/_apis/git/repositories/api"
        )

    def test_the_legacy_azure_devops_host_is_kept(self) -> None:
        from lgtmaybe.cli import gateway_builder
        from lgtmaybe.core.models import ReviewConfig

        located = parse_pr_url("https://acme.visualstudio.com/Proj/_git/api/pullrequest/4")
        build = gateway_builder(located.forge)
        assert build is not None
        gateway = build(located, "token", ReviewConfig(provider="ollama", model="llama3"))
        assert gateway._repo_api == "https://acme.visualstudio.com/Proj/_apis/git/repositories/api"

    def test_the_gitlab_builder_preserves_an_http_origin(self) -> None:
        from lgtmaybe.cli import gateway_builder
        from lgtmaybe.core.models import ReviewConfig

        located = parse_pr_url("http://gl.internal:3000/group/sub/project/-/merge_requests/9")
        build = gateway_builder(located.forge)
        assert build is not None

        gateway = build(
            located,
            "token",
            ReviewConfig(provider="ollama", model="llama3"),
        )

        assert gateway._api.startswith("http://gl.internal:3000/")
