"""Forgejo Actions names its host in FORGEJO_SERVER_URL, as well as the GITHUB_ alias.

forgejo-runner v7 and later sets each ``FORGEJO_*`` variable and its
``GITHUB_*`` alias; earlier runners set only ``GITHUB_*``. The entrypoint reads
Forgejo's own name first, so a Forgejo run never depends on the compatibility
alias, and is never mistaken for GitHub Enterprise Server.
"""

from __future__ import annotations

import pytest

from lgtmaybe.cli import pr_url_from_event
from lgtmaybe.core.forge import Forge, parse_pr_url

EVENT = {"repository": {"full_name": "owner/repo"}, "pull_request": {"number": 7}}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FORGEJO_SERVER_URL", "GITHUB_SERVER_URL", "GITHUB_API_URL"):
        monkeypatch.delenv(name, raising=False)


def test_forgejo_server_url_names_the_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEJO_SERVER_URL", "https://codeberg.org")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://codeberg.org")

    url = pr_url_from_event(EVENT)

    assert url == "https://codeberg.org/owner/repo/pulls/7"
    located = parse_pr_url(url)
    assert located.forge is Forge.gitea
    assert located.host == "codeberg.org"


def test_forgejo_server_url_wins_over_the_github_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEJO_SERVER_URL", "https://forgejo.example.com/")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")

    assert pr_url_from_event(EVENT) == "https://forgejo.example.com/owner/repo/pulls/7"


def test_an_older_runner_falls_back_to_the_github_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://forgejo.example.com")

    assert pr_url_from_event(EVENT) == "https://forgejo.example.com/owner/repo/pulls/7"


def test_a_forgejo_run_is_never_refused_as_github_enterprise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORGEJO_SERVER_URL", "https://forgejo.example.com")
    monkeypatch.setenv("GITHUB_API_URL", "https://forgejo.example.com/api/v3")

    assert pr_url_from_event(EVENT) == "https://forgejo.example.com/owner/repo/pulls/7"
