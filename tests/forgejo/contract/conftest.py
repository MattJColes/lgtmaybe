"""A real Forgejo for the Gitea adapter's contract tests.

These tests need a running Forgejo, so they carry the ``forgejo_contract``
marker and are deselected by default. ``.github/workflows/forgejo-contract.yml``
starts one in Docker; locally, ``tests/forgejo/uat/up.sh`` does too. Point them
at it with::

    FORGEJO_CONTRACT_URL=http://localhost:3000 \\
    FORGEJO_CONTRACT_ADMIN=maintainer FORGEJO_CONTRACT_PASSWORD=... \\
    uv run pytest -m forgejo_contract

The admin account sets everything up through the API: two collaborators, a
token with the three scopes the docs ask for, a repository, and one pull request
per test, so tests never see each other's comments.
"""

from __future__ import annotations

import base64
import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

pytestmark = pytest.mark.forgejo_contract

CALC = (
    "def total(xs):\n"
    "    s = 0\n"
    "    for i in range(len(xs) + 1):\n"
    "        s += xs[i]\n"
    "    return s\n"
)
LOOP_LINE = 3


@dataclass(frozen=True)
class Forgejo:
    """A provisioned instance: an admin-authenticated client and the test accounts."""

    url: str
    admin: httpx.Client
    owner: str
    token: str
    writer: str
    reader: str
    repo: str

    @property
    def host(self) -> str:
        return urlsplit(self.url).netloc

    @property
    def scheme(self) -> str:
        return urlsplit(self.url).scheme

    def api(self, path: str) -> str:
        return f"{self.url}/api/v1/repos/{self.owner}/{self.repo}{path}"

    def get(self, path: str) -> Any:
        resp = self.admin.get(self.api(path), params={"limit": 50})
        resp.raise_for_status()
        return resp.json()


@dataclass(frozen=True)
class PullRequest:
    number: int
    head_sha: str


@pytest.fixture(autouse=True)
def _authenticated_posting_identity() -> None:
    """Override the root fixture that stubs ``/api/v1/user`` as ``lgtmaybe-bot``.

    Identity is part of the contract under test: the real server must say who
    the token is, or dedupe and upserts trust nothing.
    """


def _ok(resp: httpx.Response) -> httpx.Response:
    if resp.is_error:
        raise AssertionError(
            f"{resp.request.method} {resp.request.url}: {resp.status_code} {resp.text}"
        )
    return resp


@pytest.fixture(scope="session")
def forgejo() -> Iterator[Forgejo]:
    url = os.environ.get("FORGEJO_CONTRACT_URL", "").rstrip("/")
    if not url:
        pytest.skip("FORGEJO_CONTRACT_URL is not set; no Forgejo to test against")
    owner = os.environ["FORGEJO_CONTRACT_ADMIN"]
    password = os.environ["FORGEJO_CONTRACT_PASSWORD"]
    admin = httpx.Client(auth=(owner, password), timeout=30, trust_env=False)
    suffix = secrets.token_hex(3)

    def user(name: str) -> str:
        login = f"{name}-{suffix}"
        _ok(
            admin.post(
                f"{url}/api/v1/admin/users",
                json={
                    "username": login,
                    "email": f"{login}@example.com",
                    "password": secrets.token_urlsafe(16),
                    "must_change_password": False,
                },
            )
        )
        return login

    writer, reader = user("writer"), user("reader")
    token = _ok(
        admin.post(
            f"{url}/api/v1/users/{owner}/tokens",
            json={
                "name": f"contract-{suffix}",
                "scopes": ["write:repository", "write:issue", "read:user"],
            },
        )
    ).json()["sha1"]
    repo = f"contract-{suffix}"
    _ok(
        admin.post(
            f"{url}/api/v1/user/repos",
            json={"name": repo, "auto_init": True, "default_branch": "main"},
        )
    )
    for login, permission in ((writer, "write"), (reader, "read")):
        _ok(
            admin.put(
                f"{url}/api/v1/repos/{owner}/{repo}/collaborators/{login}",
                json={"permission": permission},
            )
        )
    _ok(
        admin.post(
            f"{url}/api/v1/repos/{owner}/{repo}/labels",
            json={"name": "lgtmaybe:risk-high", "color": "#d73a4a"},
        )
    )
    yield Forgejo(url, admin, owner, token, writer, reader, repo)
    # Checked, so a leak on a long-lived instance is visible rather than silent.
    _ok(admin.delete(f"{url}/api/v1/repos/{owner}/{repo}"))
    for login in (writer, reader):
        _ok(admin.delete(f"{url}/api/v1/admin/users/{login}"))
    admin.close()


@pytest.fixture
def pull_request(forgejo: Forgejo) -> PullRequest:
    """A fresh pull request adding ``calc.py`` with an off-by-one loop."""
    branch = f"feature-{secrets.token_hex(3)}"
    _ok(
        forgejo.admin.post(
            forgejo.api("/contents/calc.py"),
            json={
                "content": base64.b64encode(CALC.encode("utf-8")).decode("ascii"),
                "message": "feat: add total",
                "new_branch": branch,
            },
        )
    )
    pr = _ok(
        forgejo.admin.post(
            forgejo.api("/pulls"),
            json={"head": branch, "base": "main", "title": "Add total", "body": "Adds a helper."},
        )
    ).json()
    return PullRequest(number=pr["number"], head_sha=pr["head"]["sha"])
