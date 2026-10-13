"""Azure Pipelines has no event payload, so the entrypoint reads predefined variables.

Like GitLab CI, an Azure Pipelines job identifies the pull request it is
validating only through its environment: the collection URI, the team project,
the repository name, and the pull request id. This is where that becomes a URL
the shared locator understands, and where the token variable picks the auth
scheme.
"""

from __future__ import annotations

import base64

import click
import pytest
from click.testing import CliRunner

from lgtmaybe.cli import RuntimeOptions, build_review_context, main, pr_url_from_pipelines_env
from lgtmaybe.core.forge import Forge, parse_pr_url
from lgtmaybe.core.models import ReviewConfig

ENV = {
    "SYSTEM_COLLECTIONURI": "https://dev.azure.com/acme/",
    "SYSTEM_TEAMPROJECT": "My Project",
    "BUILD_REPOSITORY_NAME": "api",
    "SYSTEM_PULLREQUEST_PULLREQUESTID": "42",
    "BUILD_REPOSITORY_PROVIDER": "TfsGit",
}

PR_URL = "https://dev.azure.com/acme/My%20Project/_git/api/pullrequest/42"


def _set(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    for key, value in {**ENV, **overrides}.items():
        if value is not None:
            monkeypatch.setenv(key, value)


class TestPRUrlFromPipelinesEnv:
    def test_builds_a_parseable_pull_request_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set(monkeypatch)
        url = pr_url_from_pipelines_env()

        assert url == PR_URL
        located = parse_pr_url(url)
        assert located.forge is Forge.azure_devops
        assert located.repo == "acme/My Project/api"
        assert located.number == 42

    def test_a_legacy_collection_uri_still_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set(monkeypatch, SYSTEM_COLLECTIONURI="https://acme.visualstudio.com/")
        located = parse_pr_url(pr_url_from_pipelines_env())

        assert located.host == "acme.visualstudio.com"
        assert located.repo == "acme/My Project/api"

    def test_a_build_that_is_not_for_a_pull_request_says_so(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A CI build has no pull request id; that is a pipeline setup mistake."""
        _set(monkeypatch, SYSTEM_PULLREQUEST_PULLREQUESTID=None)

        with pytest.raises(click.ClickException, match="SYSTEM_PULLREQUEST_PULLREQUESTID"):
            pr_url_from_pipelines_env()

    @pytest.mark.parametrize(
        "missing", ["SYSTEM_COLLECTIONURI", "SYSTEM_TEAMPROJECT", "BUILD_REPOSITORY_NAME"]
    )
    def test_each_missing_variable_is_named(
        self, monkeypatch: pytest.MonkeyPatch, missing: str
    ) -> None:
        _set(monkeypatch, **{missing: None})

        with pytest.raises(click.ClickException, match=missing):
            pr_url_from_pipelines_env()

    def test_a_github_repository_is_pointed_at_the_action(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Azure Pipelines can build GitHub repos, but those PRs live on GitHub."""
        _set(monkeypatch, BUILD_REPOSITORY_PROVIDER="GitHub")

        with pytest.raises(click.ClickException, match="GitHub Action"):
            pr_url_from_pipelines_env()

    @pytest.mark.parametrize("provider", [None, ""])
    def test_an_unset_repository_provider_is_treated_as_azure_repos(
        self, monkeypatch: pytest.MonkeyPatch, provider: str | None
    ) -> None:
        """Azure Pipelines always sets it; running the command by hand may not."""
        _set(monkeypatch, BUILD_REPOSITORY_PROVIDER=provider)

        assert pr_url_from_pipelines_env() == PR_URL

    def test_the_command_fails_clearly_outside_a_pull_request_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set(monkeypatch, SYSTEM_PULLREQUEST_PULLREQUESTID=None)
        result = CliRunner().invoke(
            main, ["azure-pipelines", "--provider", "ollama", "--model", "llama3"]
        )

        assert result.exit_code != 0
        assert "SYSTEM_PULLREQUEST_PULLREQUESTID" in result.output


class TestTokenSelection:
    @staticmethod
    def _gateway(monkeypatch: pytest.MonkeyPatch, **env: str | None) -> object:
        for key in ("AZURE_DEVOPS_TOKEN", "SYSTEM_ACCESSTOKEN"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            if value is not None:
                monkeypatch.setenv(key, value)
        cfg = ReviewConfig(provider="ollama", model="llama3")
        return build_review_context(cfg, RuntimeOptions(pr_url=PR_URL))[0]

    def test_a_personal_access_token_uses_basic_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gateway = self._gateway(monkeypatch, AZURE_DEVOPS_TOKEN="pat")
        expected = "Basic " + base64.b64encode(b":pat").decode()
        assert gateway._headers["Authorization"] == expected  # type: ignore[attr-defined]

    def test_the_pipeline_token_uses_bearer_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gateway = self._gateway(monkeypatch, SYSTEM_ACCESSTOKEN="job-token")
        assert gateway._headers["Authorization"] == "Bearer job-token"  # type: ignore[attr-defined]

    def test_a_personal_access_token_wins_over_the_pipeline_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gateway = self._gateway(monkeypatch, AZURE_DEVOPS_TOKEN="pat", SYSTEM_ACCESSTOKEN="job")
        assert gateway._headers["Authorization"].startswith("Basic ")  # type: ignore[attr-defined]

    def test_no_token_names_both_variables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValueError, match="AZURE_DEVOPS_TOKEN or SYSTEM_ACCESSTOKEN"):
            self._gateway(monkeypatch)
