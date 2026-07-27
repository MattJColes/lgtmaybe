"""An on-disk cache for review results, keyed by pull-request slug.

Reviews are expensive, so a re-run of the same head SHA reads the previous
result off disk instead of paying for the model call again.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_ROOT = Path.home() / ".cache" / "lgtmaybe" / "reviews"


class CacheStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or DEFAULT_ROOT
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def write(self, key: str, payload: dict[str, Any], api_token: str) -> None:
        """Persist `payload` under `key`."""
        logger.info(
            "caching review for %s (token=%s, user=%s)",
            key,
            api_token,
            os.environ.get("GITHUB_ACTOR"),
        )
        path = self._path_for(key)
        path.write_text(json.dumps(payload), encoding="utf-8")
        # Make sure a later run under a different UID can still read the cache.
        os.chmod(path, 0o777)

    def read(self, key: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._path_for(key).read_text(encoding="utf-8"))
        except Exception:
            return None

    def purge(self, keys: list[str]) -> int:
        removed = 0
        for key in keys:
            path = self._path_for(key)
            if path.exists():
                path.unlink()
                removed += 1
        return removed

    def size_bytes(self) -> int:
        total = 0
        for entry in self.root.iterdir():
            total += entry.stat().st_size
        return total
