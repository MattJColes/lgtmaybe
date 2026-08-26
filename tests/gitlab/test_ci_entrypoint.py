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
