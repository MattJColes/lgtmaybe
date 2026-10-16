"""GitLab CI has no event payload file, so the entrypoint reads CI_* variables.

This is the one place GitLab cannot reuse the GitHub Actions entrypoint: there
is no ``GITHUB_EVENT_PATH`` to open and no ``INPUT_*`` convention, so the merge
request under review has to be identified from the predefined CI variables.
"""

from __future__ import annotations

import pytest

from lgtmaybe.cli import mr_url_from_ci_env
from lgtmaybe.core.forge import Forge, parse_pr_url

ENV = {
    "CI_SERVER_HOST": "gitlab.example.com",
    "CI_PROJECT_PATH": "group/sub/project",
    "CI_MERGE_REQUEST_IID": "7",
}


def _set(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> None:
    for key in ("CI_SERVER_HOST", "CI_PROJECT_PATH", "CI_MERGE_REQUEST_IID", "CI_SERVER_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {**ENV, **overrides}.items():
        if value is not None:
            monkeypatch.setenv(key, value)


class TestMRUrlFromCIEnv:
    def test_builds_a_parseable_gitlab_merge_request_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set(monkeypatch)
        url = mr_url_from_ci_env()

        assert url == "https://gitlab.example.com/group/sub/project/-/merge_requests/7"
        located = parse_pr_url(url)
        assert located.forge is Forge.gitlab
        assert located.repo == "group/sub/project", "the nested group path survives"
        assert located.number == 7

    def test_falls_back_to_the_server_url_when_the_host_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set(monkeypatch, CI_SERVER_HOST=None, CI_SERVER_URL="https://gl.internal")
        assert mr_url_from_ci_env() == "https://gl.internal/group/sub/project/-/merge_requests/7"

    def test_preserves_the_server_urls_nonstandard_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set(
            monkeypatch,
            CI_SERVER_HOST="gl.internal",
            CI_SERVER_URL="https://gl.internal:8443",
        )

        assert (
            mr_url_from_ci_env() == "https://gl.internal:8443/group/sub/project/-/merge_requests/7"
        )

    def test_a_pipeline_that_is_not_for_a_merge_request_says_so(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A branch pipeline has no MR iid; that is a config mistake worth naming."""
        _set(monkeypatch, CI_MERGE_REQUEST_IID=None)

        with pytest.raises(Exception, match="CI_MERGE_REQUEST_IID"):
            mr_url_from_ci_env()

    def test_a_missing_project_path_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set(monkeypatch, CI_PROJECT_PATH=None)

        with pytest.raises(Exception, match="CI_PROJECT_PATH"):
            mr_url_from_ci_env()

    def test_accepts_a_server_url_at_the_host_root(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set(monkeypatch, CI_SERVER_URL="https://gitlab.example.com")

        assert mr_url_from_ci_env() == (
            "https://gitlab.example.com/group/sub/project/-/merge_requests/7"
        )

    @pytest.mark.parametrize("url", ["https://example.com/gitlab", "https://example.com/gitlab/"])
    def test_refuses_a_gitlab_served_under_a_sub_path(
        self, monkeypatch: pytest.MonkeyPatch, url: str
    ) -> None:
        """A sub-path would parse into the project path and leak GITLAB_TOKEN.

        The URL alone cannot tell a sub-path from a nested group, so the parser
        cannot refuse it; CI_SERVER_URL is the one place the difference is known,
        and refusing there stops the token before any request is made.
        """
        _set(monkeypatch, CI_SERVER_URL=url)

        with pytest.raises(Exception, match="sub-path"):
            mr_url_from_ci_env()


class TestGitLabCIIncrementalDefault:
    """GitLab CI has no ``synchronize`` action to key on, so auto means "incremental
    whenever a watermark exists": the first pipeline has none and runs a full
    review, every later push reviews only its commits. An explicit setting — the
    config file, the flag, or ``LGTMAYBE_INCREMENTAL`` on a manual pipeline (the
    GitLab spelling of ``/review full``) — always wins."""

    def _run(self, monkeypatch: pytest.MonkeyPatch, args: list[str]):
        from click.testing import CliRunner

        from lgtmaybe.cli import commands
        from lgtmaybe.cli.commands import main

        _set(monkeypatch)
        monkeypatch.delenv("LGTMAYBE_INCREMENTAL", raising=False)
        seen: list = []
        monkeypatch.setattr(commands, "execute_review", lambda cfg, runtime, **kw: seen.append(cfg))
        result = CliRunner().invoke(
            main, ["gitlab-ci", "--provider", "ollama", "--model", "llama3", *args]
        )
        assert result.exit_code == 0, result.output
        return seen[0]

    def test_unset_resolves_to_incremental(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._run(monkeypatch, []).incremental is True

    def test_the_flag_forces_a_full_review(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._run(monkeypatch, ["--no-incremental"]).incremental is False

    def test_a_pipeline_variable_forces_a_full_review(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from click.testing import CliRunner

        from lgtmaybe.cli import commands
        from lgtmaybe.cli.commands import main

        _set(monkeypatch)
        monkeypatch.setenv("LGTMAYBE_INCREMENTAL", "false")
        seen: list = []
        monkeypatch.setattr(commands, "execute_review", lambda cfg, runtime, **kw: seen.append(cfg))
        result = CliRunner().invoke(main, ["gitlab-ci", "--provider", "ollama", "--model", "m"])

        assert result.exit_code == 0, result.output
        assert seen[0].incremental is False

    def test_the_config_file_wins_over_auto(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        config = tmp_path / "lgtmaybe.yml"
        config.write_text("incremental: false\n", encoding="utf-8")

        assert self._run(monkeypatch, ["--config", str(config)]).incremental is False
