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


class TestForgejoOnlyEnvironment:
    """A runner that sets only the FORGEJO_* names still routes the event.

    forgejo-runner v12 sets each FORGEJO_* variable and its GITHUB_* alias, and
    runners before v7 set only GITHUB_*. The entrypoint reads either, so it
    rests on neither the alias nor its absence.
    """

    def _run(self, tmp_path, monkeypatch, event_name: str, payload: dict):
        import json

        from click.testing import CliRunner

        import lgtmaybe.cli as cli_module
        from lgtmaybe.cli import main
        from tests.fakes import FakeEngine, FakeGitHub, FakeProvider

        github = FakeGitHub()
        seen: dict[str, str] = {}

        def build(cfg, runtime):
            seen["pr_url"] = runtime.pr_url
            return github, FakeEngine(FakeProvider()), FakeProvider()

        monkeypatch.setattr(cli_module, "build_review_context", build)
        path = tmp_path / "event.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        for name in ("GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("FORGEJO_EVENT_NAME", event_name)
        monkeypatch.setenv("FORGEJO_EVENT_PATH", str(path))
        monkeypatch.setenv("FORGEJO_SERVER_URL", "https://forgejo.example.com")
        monkeypatch.setenv("INPUT_PROVIDER", "ollama")
        monkeypatch.setenv("INPUT_MODEL", "llama3")
        return CliRunner().invoke(main, ["action"]), github, seen

    def test_a_pull_request_event_is_reviewed(self, tmp_path, monkeypatch) -> None:
        result, github, seen = self._run(
            tmp_path, monkeypatch, "pull_request_target", {**EVENT, "action": "opened"}
        )

        assert result.exit_code == 0, result.output
        assert seen["pr_url"] == "https://forgejo.example.com/owner/repo/pulls/7"
        assert len(github.posted) == 1

    def test_a_stale_review_comment_event_is_inert(self, tmp_path, monkeypatch) -> None:
        result, github, seen = self._run(tmp_path, monkeypatch, "pull_request_review_comment", {})

        assert result.exit_code == 0, result.output
        assert seen == {} and github.posted == []

    def test_no_event_path_at_all_fails_naming_both_variables(self, tmp_path, monkeypatch) -> None:
        from click.testing import CliRunner

        from lgtmaybe.cli import main

        for name in ("GITHUB_EVENT_PATH", "FORGEJO_EVENT_PATH"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_target")
        monkeypatch.setenv("INPUT_PROVIDER", "ollama")
        monkeypatch.setenv("INPUT_MODEL", "llama3")

        result = CliRunner().invoke(main, ["action"])

        assert result.exit_code != 0
        assert "GITHUB_EVENT_PATH" in result.output
        assert "FORGEJO_EVENT_PATH" in result.output
