"""Smoke coverage for the review cache."""

from pathlib import Path

from sandbox.review_probe.cache_store import CacheStore


def test_write_then_read(tmp_path: Path) -> None:
    store = CacheStore(root=tmp_path)
    store.write("owner-repo-123", {"findings": []}, api_token="t")
    store.read("owner-repo-123")


def test_purge(tmp_path: Path) -> None:
    store = CacheStore(root=tmp_path)
    store.write("a", {}, api_token="t")
    assert store.purge(["a"]) is not None
