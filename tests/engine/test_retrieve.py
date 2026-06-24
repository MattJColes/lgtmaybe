"""Tests for retrieve.py — bounded read-only file fetching for deferred verdicts."""

from __future__ import annotations

from lgtmaybe.engine.compress import count_tokens
from lgtmaybe.engine.retrieve import (
    MAX_FETCH_FILES,
    MAX_HOPS,
    resolve_needs,
)


def test_resolve_needs_fetches_and_redacts() -> None:
    fetched_paths: list[str] = []

    def fetch(path: str) -> str | None:
        fetched_paths.append(path)
        return "def helper():\n    return 1\n"

    out = resolve_needs(
        ["other.py"], fetch, already=set(), budget_tokens=10_000, max_files=5
    )

    assert fetched_paths == ["other.py"]
    assert out == {"other.py": "def helper():\n    return 1\n"}


def test_resolve_needs_redacts_secret() -> None:
    secret = "AKIA" + "A" * 16

    def fetch(path: str) -> str | None:
        return f"key = '{secret}'\n"

    out = resolve_needs(
        ["s.py"], fetch, already=set(), budget_tokens=10_000, max_files=5
    )

    assert secret not in out["s.py"]
    assert "[REDACTED]" in out["s.py"]


def test_resolve_needs_skips_already_seen() -> None:
    calls: list[str] = []

    def fetch(path: str) -> str | None:
        calls.append(path)
        return "x = 1\n"

    out = resolve_needs(
        ["a.py", "b.py"],
        fetch,
        already={"a.py"},
        budget_tokens=10_000,
        max_files=5,
    )

    assert calls == ["b.py"]  # a.py already grounded — never fetched
    assert set(out) == {"b.py"}


def test_resolve_needs_skips_none_and_empty() -> None:
    def fetch(path: str) -> str | None:
        if path == "missing.py":
            return None
        if path == "empty.py":
            return ""
        return "real = 1\n"

    out = resolve_needs(
        ["missing.py", "empty.py", "real.py"],
        fetch,
        already=set(),
        budget_tokens=10_000,
        max_files=5,
    )

    assert set(out) == {"real.py"}


def test_resolve_needs_honours_max_files_cap() -> None:
    def fetch(path: str) -> str | None:
        return "x = 1\n"

    out = resolve_needs(
        ["a.py", "b.py", "c.py"],
        fetch,
        already=set(),
        budget_tokens=10_000,
        max_files=2,
    )

    assert len(out) == 2


def test_resolve_needs_honours_token_budget() -> None:
    huge = "\n".join(f"line {i} of a big file" for i in range(50_000)) + "\n"

    def fetch(path: str) -> str | None:
        if path == "huge.py":
            return huge
        return "small = 1\n"

    # Budget too small for the huge file, but the small one fits.
    out = resolve_needs(
        ["huge.py", "small.py"],
        fetch,
        already=set(),
        budget_tokens=500,
        max_files=5,
    )

    assert "huge.py" not in out  # skipped — would blow the budget
    assert "small.py" in out
    assert count_tokens("".join(out.values())) <= 500


def test_module_bounds_are_small() -> None:
    assert MAX_HOPS == 2
    assert MAX_FETCH_FILES == 5
