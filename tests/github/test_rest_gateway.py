"""Tests for RestGitHubGateway.get_pr_context — respx-mocked GitHub REST API."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from lgtmaybe.github import RestGitHubGateway

FIXTURES = Path(__file__).parent / "fixtures"

REPO = "owner/repo"
PR_NUMBER = 42
TOKEN = "ghp_test"

BASE_URL = "https://api.github.com"
PR_URL = f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}"
FILES_URL = f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/files"
COMMITS_URL = f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/commits"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


def _load_json(name: str) -> object:
    return json.loads(_load(name))


@respx.mock
def test_get_pr_context_returns_expected_shas_and_diff() -> None:
    """get_pr_context fetches the PR diff and extracts base/head SHAs."""
    # Route matching is first-match; register the more-specific diff route first.
    respx.route(
        method="GET",
        url=PR_URL,
        headers={"Accept": "application/vnd.github.v3.diff"},
    ).mock(return_value=httpx.Response(200, content=_load("pr_diff.patch").encode()))
    respx.route(
        method="GET",
        url=PR_URL,
    ).mock(return_value=httpx.Response(200, json=_load_json("pr_detail.json")))
    respx.route(
        method="GET",
        url__startswith=FILES_URL,
    ).mock(return_value=httpx.Response(200, json=_load_json("pr_files_page1.json")))
    respx.route(
        method="GET",
        url__startswith=COMMITS_URL,
    ).mock(return_value=httpx.Response(200, json=_load_json("pr_commits.json")))
    respx.route(
        method="GET",
        url__startswith=f"{BASE_URL}/repos/{REPO}/contents/",
    ).mock(return_value=httpx.Response(200, text="raw file content"))

    client = httpx.Client()
    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=client)
    ctx = gw.get_pr_context()

    assert ctx.base_sha == "abc1234base"
    assert ctx.head_sha == "def5678head"
    assert ctx.repo == REPO
    assert ctx.pr_number == PR_NUMBER
    assert "src/app.py" in ctx.diff


@respx.mock
def test_get_pr_context_raises_clear_error_when_metadata_lacks_shas() -> None:
    """A PR-detail response missing base/head must surface a clear error.

    Regression: ``meta["base"]["sha"]`` raised a bare ``KeyError`` on a
    malformed/partial GitHub response — an opaque traceback rather than the
    clear, surfaced error the project requires. Should raise with a message that
    names the missing base/head SHA, not a KeyError.
    """
    respx.route(method="GET", url=PR_URL).mock(return_value=httpx.Response(200, json={}))

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    with pytest.raises(RuntimeError, match="base/head"):
        gw.get_pr_context()


@respx.mock
def test_get_pr_context_paginates_files_list() -> None:
    """get_pr_context follows Link rel=next to retrieve all files across pages."""
    page1_url = f"{FILES_URL}?per_page=100"
    page2_url = f"{FILES_URL}?per_page=100&page=2"

    link_header = f'<{page2_url}>; rel="next", <{page2_url}>; rel="last"'

    respx.route(
        method="GET",
        url=PR_URL,
        headers={"Accept": "application/vnd.github.v3.diff"},
    ).mock(return_value=httpx.Response(200, content=_load("pr_diff.patch").encode()))
    respx.route(
        method="GET",
        url=PR_URL,
    ).mock(return_value=httpx.Response(200, json=_load_json("pr_detail.json")))
    respx.route(method="GET", url=page1_url).mock(
        return_value=httpx.Response(
            200,
            json=_load_json("pr_files_page1.json"),
            headers={"Link": link_header},
        )
    )
    respx.route(method="GET", url=page2_url).mock(
        return_value=httpx.Response(200, json=_load_json("pr_files_page2.json"))
    )
    respx.route(
        method="GET",
        url__startswith=COMMITS_URL,
    ).mock(return_value=httpx.Response(200, json=_load_json("pr_commits.json")))
    respx.route(
        method="GET",
        url__startswith=f"{BASE_URL}/repos/{REPO}/contents/",
    ).mock(return_value=httpx.Response(200, text="raw file content"))

    client = httpx.Client()
    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=client)
    ctx = gw.get_pr_context()

    # Page 1 files
    assert "src/app.py" in ctx.changed_files
    assert "package-lock.json" in ctx.changed_files
    # Page 2 files
    assert "src/models.py" in ctx.changed_files
    assert "yarn.lock" in ctx.changed_files


def _base_routes(*, commits_status: int = 200) -> None:
    """Register the meta/diff/files/commits routes shared by the remaining tests."""
    respx.route(
        method="GET", url=PR_URL, headers={"Accept": "application/vnd.github.v3.diff"}
    ).mock(return_value=httpx.Response(200, content=_load("pr_diff.patch").encode()))
    respx.route(method="GET", url=PR_URL).mock(
        return_value=httpx.Response(200, json=_load_json("pr_detail.json"))
    )
    respx.route(method="GET", url__startswith=FILES_URL).mock(
        return_value=httpx.Response(200, json=_load_json("pr_files_page1.json"))
    )
    respx.route(method="GET", url__startswith=COMMITS_URL).mock(
        return_value=httpx.Response(commits_status, json=_load_json("pr_commits.json"))
    )


def _contents_route(path: str):
    return respx.route(method="GET", url__startswith=f"{BASE_URL}/repos/{REPO}/contents/{path}")


@respx.mock
def test_get_pr_context_fetches_reviewable_file_contents() -> None:
    """Head content is fetched for reviewable files and skipped for the rest."""
    _base_routes()
    _contents_route("src/app.py").mock(
        return_value=httpx.Response(200, text="import os\nimport sys\n")
    )
    _contents_route("src/utils.py").mock(
        return_value=httpx.Response(200, text="def helper():\n    return 1\n")
    )
    lock_route = _contents_route("package-lock.json").mock(
        return_value=httpx.Response(200, text="{}")
    )

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    ctx = gw.get_pr_context()

    assert ctx.file_contents["src/app.py"] == "import os\nimport sys\n"
    assert ctx.file_contents["src/utils.py"].startswith("def helper")
    # Lockfiles and minified bundles are never fetched.
    assert "package-lock.json" not in ctx.file_contents
    assert "app.min.js" not in ctx.file_contents
    assert not lock_route.called


@respx.mock
def test_get_pr_context_skips_unfetchable_file() -> None:
    """A 404 (deleted/renamed) file is skipped, not fatal."""
    _base_routes()
    _contents_route("src/app.py").mock(return_value=httpx.Response(404))
    _contents_route("src/utils.py").mock(return_value=httpx.Response(200, text="ok"))

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    ctx = gw.get_pr_context()

    assert "src/app.py" not in ctx.file_contents
    assert ctx.file_contents["src/utils.py"] == "ok"


# ---------------------------------------------------------------------------
# Stated intent: PR title / description / commit names for the intent lens
# ---------------------------------------------------------------------------


def _all_contents_ok() -> None:
    respx.route(method="GET", url__startswith=f"{BASE_URL}/repos/{REPO}/contents/").mock(
        return_value=httpx.Response(200, text="raw file content")
    )


@respx.mock
def test_get_pr_context_includes_stated_intent() -> None:
    """Title, description, and commit names (first lines only) ride along so the
    engine's intent lens can judge whether the diff does what the PR claims."""
    _base_routes()
    _all_contents_ok()

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    ctx = gw.get_pr_context()

    assert ctx.title == "Add rate limiting"
    assert ctx.description == "Limits login attempts per IP."
    # First line of each commit message only — the commit "name".
    assert ctx.commit_messages == ["feat: add rate limiting", "fix: handle empty bucket"]


@respx.mock
def test_get_pr_context_degrades_when_commits_fetch_fails() -> None:
    """Commit names are auxiliary intent context: a failed fetch degrades to an
    empty list (like file contents) instead of failing the whole review."""
    _base_routes(commits_status=500)
    _all_contents_ok()

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    ctx = gw.get_pr_context()

    assert ctx.commit_messages == []
    assert ctx.title == "Add rate limiting"  # the rest of the context is intact


@respx.mock
def test_base_checkout_root_clones_once_and_caches(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The base tree is cloned at most once per gateway and the result is cached:
    a second symbol deferral reuses it instead of re-cloning."""
    respx.route(method="GET", url=PR_URL).mock(
        return_value=httpx.Response(200, json={"base": {"ref": "main"}})
    )

    calls: list[tuple[str, str]] = []

    def fake_clone(repo: str, ref: str, token: str, **_: object) -> Path:
        calls.append((repo, ref))
        return Path("/tmp/fake-base")

    monkeypatch.setattr("lgtmaybe.github.rest_gateway.clone_base_tree", fake_clone)

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    first = gw.base_checkout_root()
    second = gw.base_checkout_root()

    assert first == Path("/tmp/fake-base")
    assert second == first
    assert calls == [(REPO, "main")]  # cloned exactly once, with the base ref


@respx.mock
def test_base_checkout_root_returns_none_when_ref_unavailable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """If the base ref can't be fetched, no clone is attempted and None is returned."""
    respx.route(method="GET", url=PR_URL).mock(return_value=httpx.Response(500))

    cloned = False

    def fake_clone(*_a: object, **_k: object) -> Path:
        nonlocal cloned
        cloned = True
        return Path("/x")

    monkeypatch.setattr("lgtmaybe.github.rest_gateway.clone_base_tree", fake_clone)

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    assert gw.base_checkout_root() is None
    assert cloned is False  # never tried to clone without a ref
