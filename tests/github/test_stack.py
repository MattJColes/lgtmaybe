"""Where a pull request sits in a stack, read by the GitHub gateway.

Two shapes, one result (`PRContext.stack`):

- GitHub's native stacked pull requests: the PR resource carries a ``stack``
  object, and the stacks API lists every layer bottom to top;
- a chain of PRs each based on the branch of the one below (Graphite, ghstack,
  git-town and friends): walked through the pulls list by base and head branch.

Best-effort context: a failure leaves ``stack`` None and the review goes on.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import respx

from lgtmaybe.github import RestGitHubGateway

REPO = "owner/repo"
PR_NUMBER = 42
TOKEN = "ghp_test"
API = f"https://api.github.com/repos/{REPO}"
PR_PATH = f"/repos/{REPO}/pulls/{PR_NUMBER}"

_DIFF = (
    "diff --git a/src/api.py b/src/api.py\n--- a/src/api.py\n+++ b/src/api.py\n"
    "@@ -1 +1,2 @@\n x = 1\n+y = 2\n"
)

_TITLES = {41: "Add the user model", 43: "Wire the API", 44: "Add the UI"}
_FILES = {41: ["src/models.py"], 43: ["src/api_routes.py"], 44: ["web/app.ts"]}


def _meta(**overrides: object) -> dict[str, object]:
    meta: dict[str, object] = {
        "title": "Add the service layer",
        "body": "",
        "base": {"sha": "b" * 40, "ref": "main", "repo": {"default_branch": "main"}},
        "head": {"sha": "h" * 40, "ref": "feature-b", "repo": {"full_name": REPO}},
    }
    meta.update(overrides)
    return meta


def _pr_item(number: int, *, base: str, head: str) -> dict[str, object]:
    return {
        "number": number,
        "title": _TITLES[number],
        "base": {"ref": base},
        "head": {"ref": head, "repo": {"full_name": REPO}},
    }


class _GitHub:
    """One dispatcher for every GET the context fetch makes."""

    def __init__(
        self,
        meta: dict[str, object],
        *,
        pulls: Callable[[httpx.QueryParams], list[dict[str, object]]] = lambda _q: [],
        stacks: dict[int, dict[str, object]] | None = None,
        stacks_status: int = 200,
    ) -> None:
        self.meta = meta
        self.pulls = pulls
        self.stacks = stacks or {}
        self.stacks_status = stacks_status
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(f"{path}?{request.url.query.decode()}")
        if request.method == "POST":  # the open-thread count: best-effort
            return httpx.Response(500)
        if path == PR_PATH:
            if "diff" in request.headers.get("Accept", ""):
                return httpx.Response(200, text=_DIFF)
            return httpx.Response(200, json=self.meta)
        if path == f"{PR_PATH}/files":
            return httpx.Response(200, json=[{"filename": "src/api.py", "status": "modified"}])
        if path == f"{PR_PATH}/commits":
            return httpx.Response(200, json=[])
        if path.startswith(f"/repos/{REPO}/contents/"):
            return httpx.Response(200, text="x = 1\ny = 2\n")
        if path.startswith(f"/repos/{REPO}/stacks/"):
            if self.stacks_status != 200:
                return httpx.Response(self.stacks_status)
            return httpx.Response(200, json=self.stacks[int(path.rsplit("/", 1)[1])])
        if path == f"/repos/{REPO}/pulls":
            return httpx.Response(200, json=self.pulls(request.url.params))
        number = int(path.split("/pulls/")[1].split("/")[0])
        if path.endswith("/files"):
            return httpx.Response(
                200, json=[{"filename": f, "status": "modified"} for f in _FILES[number]]
            )
        return httpx.Response(200, json={"number": number, "title": _TITLES[number]})


def _context(github: _GitHub):  # type: ignore[no-untyped-def]
    with respx.mock:
        respx.route(url__startswith="https://api.github.com/").mock(side_effect=github)
        return RestGitHubGateway(
            repo=REPO, pr_number=PR_NUMBER, token=TOKEN, client=httpx.Client()
        ).get_pr_context()


def test_a_native_stack_names_every_other_open_layer() -> None:
    meta = _meta(
        base={"sha": "b" * 40, "ref": "feature-a", "repo": {"default_branch": "main"}},
        stack={"id": 9, "number": 7, "size": 3, "position": 2, "base": {"ref": "main"}},
    )
    stacks = {
        7: {
            "number": 7,
            "base": {"ref": "main"},
            "pull_requests": [
                {"number": 41, "state": "open", "head": {"ref": "feature-a"}},
                {"number": 42, "state": "open", "head": {"ref": "feature-b"}},
                {"number": 43, "state": "open", "head": {"ref": "feature-c"}},
            ],
        }
    }

    ctx = _context(_GitHub(meta, stacks=stacks))

    assert ctx.stack is not None
    assert (ctx.stack.position, ctx.stack.size, ctx.stack.base_ref) == (2, 3, "main")
    assert [(layer.number, layer.title, layer.files) for layer in ctx.stack.below] == [
        (41, "Add the user model", ["src/models.py"])
    ]
    assert [layer.number for layer in ctx.stack.above] == [43]


def test_a_chain_of_prs_based_on_each_other_is_a_stack() -> None:
    """Graphite, ghstack and git-town stack PRs by pointing each one's base at the
    branch of the PR below it, with no native stack object."""
    meta = _meta(base={"sha": "b" * 40, "ref": "feature-a", "repo": {"default_branch": "main"}})

    def pulls(query: httpx.QueryParams) -> list[dict[str, object]]:
        if query.get("head") == "owner:feature-a":
            return [_pr_item(41, base="main", head="feature-a")]
        if query.get("base") == "feature-b":
            return [_pr_item(43, base="feature-b", head="feature-c")]
        if query.get("base") == "feature-c":
            return [_pr_item(44, base="feature-c", head="feature-d")]
        return []

    ctx = _context(_GitHub(meta, pulls=pulls))

    assert ctx.stack is not None
    assert (ctx.stack.position, ctx.stack.size, ctx.stack.base_ref) == (2, 4, "main")
    assert [layer.number for layer in ctx.stack.below] == [41]
    assert [layer.number for layer in ctx.stack.above] == [43, 44]
    assert ctx.stack.above[1].files == ["web/app.ts"]


def test_the_bottom_of_a_chain_knows_what_is_built_on_it() -> None:
    def pulls(query: httpx.QueryParams) -> list[dict[str, object]]:
        if query.get("base") == "feature-b":
            return [_pr_item(43, base="feature-b", head="feature-c")]
        return []

    ctx = _context(_GitHub(_meta(), pulls=pulls))

    assert ctx.stack is not None
    assert (ctx.stack.position, ctx.stack.size, ctx.stack.base_ref) == (1, 2, "main")


def test_a_standalone_pr_has_no_stack_and_costs_one_lookup() -> None:
    github = _GitHub(_meta())

    ctx = _context(github)

    assert ctx.stack is None
    assert [p for p in github.paths if p.startswith(f"/repos/{REPO}/pulls?")] == [
        f"/repos/{REPO}/pulls?state=open&base=feature-b&per_page=2"
    ]


def test_a_fork_pr_is_never_walked() -> None:
    """A fork's branch names say nothing about this repository's branches: a PR
    here based on a branch that happens to share the fork's name is not a layer."""
    github = _GitHub(
        _meta(head={"sha": "h" * 40, "ref": "feature-b", "repo": {"full_name": "fork/repo"}})
    )

    ctx = _context(github)

    assert ctx.stack is None
    assert not [p for p in github.paths if p.startswith(f"/repos/{REPO}/pulls?")]


def test_a_failed_stack_lookup_never_fails_the_review() -> None:
    meta = _meta(stack={"id": 9, "number": 7, "size": 3, "position": 2, "base": {"ref": "main"}})

    ctx = _context(_GitHub(meta, stacks_status=500))

    assert ctx.stack is None
    assert "src/api.py" in ctx.diff


def test_layer_lookups_are_capped() -> None:
    """A runaway chain (or a cycle) must not turn one review into hundreds of calls."""

    def pulls(query: httpx.QueryParams) -> list[dict[str, object]]:
        base = query.get("base") or ""
        if base.startswith("feature-"):
            return [
                {
                    "number": 43,
                    "title": "loop",
                    "base": {"ref": base},
                    "head": {"ref": f"{base}x", "repo": {"full_name": REPO}},
                }
            ]
        return []

    github = _GitHub(_meta(), pulls=pulls)

    ctx = _context(github)

    assert ctx.stack is not None
    assert len(ctx.stack.above) <= 8
    assert ctx.stack.complete is False  # the cap cut it short: size is a lower bound
    assert json.dumps(ctx.stack.model_dump())  # serialisable, bounded


def test_a_chain_walked_to_both_ends_is_complete() -> None:
    def pulls(query: httpx.QueryParams) -> list[dict[str, object]]:
        if query.get("base") == "feature-b":
            return [_pr_item(43, base="feature-b", head="feature-c")]
        return []

    ctx = _context(_GitHub(_meta(), pulls=pulls))

    assert ctx.stack is not None
    assert ctx.stack.complete is True


def test_an_ambiguous_step_ends_the_walk_and_leaves_the_stack_incomplete() -> None:
    """Two open PRs share the head branch below: the walk cannot tell which is the
    layer, so it stops there and claims no position."""
    meta = _meta(base={"sha": "b" * 40, "ref": "feature-a", "repo": {"default_branch": "main"}})

    def pulls(query: httpx.QueryParams) -> list[dict[str, object]]:
        if query.get("head") == "owner:feature-a":
            return [
                _pr_item(41, base="main", head="feature-a"),
                _pr_item(44, base="main", head="feature-a"),
            ]
        if query.get("base") == "feature-b":
            return [_pr_item(43, base="feature-b", head="feature-c")]
        return []

    ctx = _context(_GitHub(meta, pulls=pulls))

    assert ctx.stack is not None
    assert ctx.stack.below == []
    assert ctx.stack.complete is False


def test_a_child_from_a_fork_is_not_a_layer() -> None:
    def pulls(query: httpx.QueryParams) -> list[dict[str, object]]:
        if query.get("base") == "feature-b":
            item = _pr_item(43, base="feature-b", head="feature-c")
            item["head"] = {"ref": "feature-c", "repo": {"full_name": "fork/repo"}}
            return [item]
        return []

    ctx = _context(_GitHub(_meta(), pulls=pulls))

    assert ctx.stack is None


def test_a_native_stack_counts_position_from_the_layers_it_lists() -> None:
    """The payload's position can count layers the listing no longer holds open;
    the block names the listing's layers, so its position must agree with them."""
    meta = _meta(
        base={"sha": "b" * 40, "ref": "feature-a", "repo": {"default_branch": "main"}},
        stack={"id": 9, "number": 7, "size": 4, "position": 3, "base": {"ref": "main"}},
    )
    stacks = {
        7: {
            "number": 7,
            "base": {"ref": "main"},
            "pull_requests": [
                {"number": 40, "state": "closed", "head": {"ref": "feature-0"}},
                {"number": 41, "state": "open", "head": {"ref": "feature-a"}},
                {"number": 42, "state": "open", "head": {"ref": "feature-b"}},
                {"number": 43, "state": "open", "head": {"ref": "feature-c"}},
            ],
        }
    }

    ctx = _context(_GitHub(meta, stacks=stacks))

    assert ctx.stack is not None
    assert (ctx.stack.position, ctx.stack.size) == (2, 3)


def test_layers_are_fetched_concurrently() -> None:
    """Each layer costs a lookup or two, independent of the others: a stack of
    several layers must not pay them one after another on the context fetch."""
    import threading

    meta = _meta(
        base={"sha": "b" * 40, "ref": "feature-a", "repo": {"default_branch": "main"}},
        stack={"id": 9, "number": 7, "size": 3, "position": 2, "base": {"ref": "main"}},
    )
    stacks = {
        7: {
            "number": 7,
            "base": {"ref": "main"},
            "pull_requests": [
                {"number": 41, "state": "open", "head": {"ref": "feature-a"}},
                {"number": 42, "state": "open", "head": {"ref": "feature-b"}},
                {"number": 43, "state": "open", "head": {"ref": "feature-c"}},
            ],
        }
    }
    both_in_flight = threading.Barrier(2, timeout=5)

    class _Concurrent(_GitHub):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path in (f"/repos/{REPO}/pulls/41", f"/repos/{REPO}/pulls/43"):
                both_in_flight.wait()  # a serial fetch never gets the second one here
            return super().__call__(request)

    ctx = _context(_Concurrent(meta, stacks=stacks))

    assert ctx.stack is not None
    assert [layer.number for layer in ctx.stack.below + ctx.stack.above] == [41, 43]
