"""Gitea Actions reuses GitHub Actions' env contract, so the entrypoint mostly works.

The one thing it cannot inherit is the host: Gitea Actions sets the same
``GITHUB_*`` variables, but ``GITHUB_SERVER_URL`` points at the Gitea instance.
Reading it is what makes the difference between reviewing the right PR and
trying to post to github.com.
"""

from __future__ import annotations

import pytest

from lgtmaybe.cli import pr_url_from_event
from lgtmaybe.core.forge import Forge, parse_pr_url

EVENT = {"repository": {"full_name": "team/service"}, "pull_request": {"number": 12}}


class TestPRUrlFromEvent:
    def test_defaults_to_github_when_no_server_url_is_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)
        assert pr_url_from_event(EVENT) == "https://github.com/team/service/pull/12"

    def test_an_explicit_github_server_url_still_resolves_to_github(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
        url = pr_url_from_event(EVENT)
        assert parse_pr_url(url).forge is Forge.github

    def test_a_gitea_server_url_produces_a_gitea_pull_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Gitea pluralises the segment, which is also how the forge is told apart."""
        monkeypatch.setenv("GITHUB_SERVER_URL", "https://gitea.example.com")
        monkeypatch.setenv("GITHUB_API_URL", "https://gitea.example.com/api/v1")
        url = pr_url_from_event(EVENT)

        assert url == "https://gitea.example.com/team/service/pulls/12"
        located = parse_pr_url(url)
        assert located.forge is Forge.gitea
        assert located.host == "gitea.example.com"
        assert located.repo == "team/service"
        assert located.number == 12

    def test_github_enterprise_is_rejected_instead_of_misclassified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.example.com")
        monkeypatch.setenv("GITHUB_API_URL", "https://github.example.com/api/v3")

        with pytest.raises(Exception, match="GitHub Enterprise Server is not supported"):
            pr_url_from_event(EVENT)

    def test_a_trailing_slash_on_the_server_url_does_not_break_the_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_SERVER_URL", "https://gitea.example.com/")
        assert pr_url_from_event(EVENT) == "https://gitea.example.com/team/service/pulls/12"

    def test_a_malformed_payload_still_fails_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_SERVER_URL", "https://gitea.example.com")
        with pytest.raises(Exception, match="missing required field"):
            pr_url_from_event({"repository": {}})
