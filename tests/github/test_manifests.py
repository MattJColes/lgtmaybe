"""Dependency manifests reach the scanners without reaching the review.

A lockfile is deliberately NOT reviewable: nobody wants a model commenting on
line 84,000 of package-lock.json, and `is_reviewable` has always dropped it. But
a vulnerability scanner needs exactly those files. So they travel in their own
channel — `PRContext.scan_contents` — which the diff, the prompts, hint blocks
and reflection never read.
"""

from __future__ import annotations

from lgtmaybe.github.diff import is_reviewable, is_scannable_manifest


def test_lockfiles_are_scannable_but_never_reviewable() -> None:
    for path in (
        "package-lock.json",
        "poetry.lock",
        "uv.lock",
        "Cargo.lock",
        "go.sum",
        "frontend/yarn.lock",
    ):
        assert is_scannable_manifest(path), path
        assert not is_reviewable(path), f"{path} must stay out of the review"


def test_manifests_are_scannable_and_still_reviewable() -> None:
    """A manifest is human-written, so it stays under review as well as scanned."""
    for path in ("pyproject.toml", "package.json", "go.mod", "requirements.txt"):
        assert is_scannable_manifest(path), path
        assert is_reviewable(path), path


def test_ordinary_source_is_not_a_manifest() -> None:
    for path in ("src/app.py", "README.md", "requirements_helper.py"):
        assert not is_scannable_manifest(path), path


def test_scan_contents_never_reaches_the_model() -> None:
    """The requirement this channel exists for.

    `file_contents` feeds hunk expansion, suppression pragmas and reflection
    grounding — all of which end up in a prompt. A lockfile must reach none of
    them, so it travels in `scan_contents` and only the tool runner reads it.
    """
    from lgtmaybe.core.models import PRContext, Provider, ReviewConfig
    from lgtmaybe.engine import LLMReviewEngine
    from tests.fakes import FakeProvider

    lockfile_line = "resolved-package-9.9.9-with-a-distinctive-marker"
    ctx = PRContext(
        diff=(
            "diff --git a/src/app.py b/src/app.py\n@@ -1 +1,2 @@\n old\n+new\n"
            "diff --git a/uv.lock b/uv.lock\n@@ -1 +1,2 @@\n a\n+" + lockfile_line + "\n"
        ),
        changed_files=["src/app.py", "uv.lock"],
        base_sha="a",
        head_sha="b",
        repo="org/repo",
        pr_number=1,
        file_contents={"src/app.py": "old\nnew\n"},
        scan_contents={"uv.lock": f"a\n{lockfile_line}\n"},
    )
    provider = FakeProvider()
    LLMReviewEngine(provider).review(ctx, ReviewConfig(provider=Provider.ollama, model="llama3"))

    sent = "\n".join(
        str(message.get("content")) for call in provider.calls for message in call["messages"]
    )
    assert lockfile_line not in sent
