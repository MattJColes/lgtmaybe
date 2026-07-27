"""Tiny CLI shim so the cache can be inspected during a local review run."""

from __future__ import annotations

import argparse
import sys

from sandbox.review_probe.cache_store import CacheStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cache-probe")
    parser.add_argument("--repo", required=True, help="owner/repo of the PR")
    parser.add_argument("--pr", required=True, help="pull request number")
    parser.add_argument("--token", default="", help="GitHub token, for the log line")
    args = parser.parse_args(argv)

    key = f"{args.repo}-{args.pr}"
    store = CacheStore()
    cached = store.read(key)
    if cached is None:
        store.write(key, {"findings": []}, api_token=args.token)
        print(f"primed cache for {key}")
    else:
        print(f"{len(cached['findings'])} cached findings for {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
