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


@respx.mock
def test_open_thread_count_overlaps_the_file_fetches() -> None:
    """The count is disclosure metadata, so it must not sit in FRONT of the
    content fetches — it shares nothing with them and rides the same pool. Run
    serially it would add a round trip to every review's startup."""
    import threading

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
        return_value=httpx.Response(200, json=_load_json("pr_commits.json"))
    )

    lock = threading.Lock()
    events: list[str] = []

    def slow(label: str):
        def handler(request: httpx.Request) -> httpx.Response:
            with lock:
                events.append(f"start-{label}")
            threading.Event().wait(0.05)
            with lock:
                events.append(f"end-{label}")
            if label == "threads":
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                        "nodes": [],
                                    }
                                }
                            }
                        }
                    },
                )
            return httpx.Response(200, text="raw file content")

        return handler

    respx.route(method="POST", url="https://api.github.com/graphql").mock(
        side_effect=slow("threads")
    )
    respx.route(method="GET", url__startswith=f"{BASE_URL}/repos/{REPO}/contents/").mock(
        side_effect=slow("content")
    )

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    gw.get_pr_context()

    assert "start-threads" in events, "the thread count never ran"
    first_end = next(i for i, e in enumerate(events) if e.startswith("end-"))
    assert len([e for e in events[:first_end] if e.startswith("start-")]) >= 2, (
        f"the thread count did not overlap the content fetches: {events}"
    )


@respx.mock
def test_content_fetches_keep_full_concurrency_while_the_count_runs() -> None:
    """The count rides the pool on a dedicated worker, not a borrowed one.

    Sizing the pool at `_CONTENT_FETCH_WORKERS` let the count take a slot from
    the content fetches, so a PR with at least that many reviewable files
    fetched content one worker narrower for as long as the count ran — the
    overlap became a slowdown on exactly the wide PRs the sizing exists for.

    Asserted as a property (how many fetches are actually in flight together),
    not by mocking the executor to read back its `max_workers`: the latter only
    restates the line of code, and would still pass if the count were moved
    somewhere that genuinely stole capacity.
    """
    import threading

    from lgtmaybe.github.rest_gateway import _CONTENT_FETCH_WORKERS

    files = [{"filename": f"src/mod{i}.py", "status": "modified"} for i in range(20)]
    respx.route(
        method="GET", url=PR_URL, headers={"Accept": "application/vnd.github.v3.diff"}
    ).mock(return_value=httpx.Response(200, content=_load("pr_diff.patch").encode()))
    respx.route(method="GET", url=PR_URL).mock(
        return_value=httpx.Response(200, json=_load_json("pr_detail.json"))
    )
    respx.route(method="GET", url__startswith=FILES_URL).mock(
        return_value=httpx.Response(200, json=files)
    )
    respx.route(method="GET", url__startswith=COMMITS_URL).mock(
        return_value=httpx.Response(200, json=_load_json("pr_commits.json"))
    )

    lock = threading.Lock()
    in_flight = 0
    peak = 0

    def content(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        threading.Event().wait(0.02)
        with lock:
            in_flight -= 1
        return httpx.Response(200, text="raw file content")

    def slow_count(request: httpx.Request) -> httpx.Response:
        # Outlives the content fetches, so it is in flight for all of them.
        threading.Event().wait(0.2)
        return httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [],
                            }
                        }
                    }
                }
            },
        )

    respx.route(method="GET", url__startswith=f"{BASE_URL}/repos/{REPO}/contents/").mock(
        side_effect=content
    )
    respx.route(method="POST", url="https://api.github.com/graphql").mock(side_effect=slow_count)

    gw = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client())
    gw.get_pr_context()

    assert peak >= _CONTENT_FETCH_WORKERS, (
        f"content fetches peaked at {peak} concurrent while the count ran; "
        f"the count is taking a worker from them (expected {_CONTENT_FETCH_WORKERS})"
    )
