"""Load and merge config layers with CLI overrides into a ReviewConfig.

Precedence (highest to lowest):
  1. Explicit CLI / action inputs (only keys whose value is not None)
  2. Repo config file (.lgtmaybe.yml)
  3. User-level config (~/.config/lgtmaybe/config.yml), when a path is given
  4. Built-in defaults (provider=ollama, model=llama3)
"""

from __future__ import annotations

from pathlib import Path
from typing import IO, Any

import yaml

from lgtmaybe.config import store
from lgtmaybe.core.models import ReviewConfig

_DEFAULTS: dict[str, Any] = {
    "provider": "ollama",
    "model": "llama3",
}


def load_config(
    *,
    config_path: Path | None = None,
    config_stream: IO[str] | None = None,
    user_config_path: Path | None = None,
    **cli_inputs: Any,
) -> ReviewConfig:
    """Return a ReviewConfig by merging defaults, configs, and CLI inputs.

    Pass explicit CLI values as keyword arguments — only non-None values
    override lower-precedence layers.  Supply a ``config_path`` (repo file) and/or
    ``user_config_path`` (user-level file); the user layer sits below the repo
    file. ``config_stream`` is an alternative repo-file source for testing.
    """
    merged: dict[str, Any] = dict(_DEFAULTS)

    if user_config_path is not None:
        merged.update(store.load(user_config_path))

    file_data = _load_file(config_path, config_stream)
    merged.update(file_data)

    # CLI inputs win only when the caller actually supplied a value.
    for key, value in cli_inputs.items():
        if value is not None:
            merged[key] = value

    # `lens_paths` is a loader directive, not a ReviewConfig field: resolve the
    # referenced skill files into extra_lenses, then drop the key (the strict
    # ReviewConfig would otherwise reject it).
    lens_paths = merged.pop("lens_paths", None)
    if lens_paths:
        loaded = _load_lens_files(lens_paths)
        merged["extra_lenses"] = list(merged.get("extra_lenses") or []) + loaded

    return ReviewConfig.model_validate(merged)


def _load_lens_files(lens_paths: Any) -> list[dict[str, Any]]:
    """Load custom-lens definitions from skill files / directories.

    Each path is a YAML file (one lens, or a list of lenses) or a directory of
    ``*.yml`` / ``*.yaml`` lens files. Returns plain dicts — pydantic validates
    them as ``CustomLens`` when the ReviewConfig is built. Lens files are trusted
    config (committed to the repo), never PR-author content.
    """
    paths = [lens_paths] if isinstance(lens_paths, str) else list(lens_paths)
    lenses: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            files = sorted(p for ext in ("*.yml", "*.yaml") for p in path.glob(ext))
        else:
            files = [path]
        for file in files:
            parsed = yaml.safe_load(file.read_text())
            if isinstance(parsed, list):
                lenses.extend(item for item in parsed if isinstance(item, dict))
            elif isinstance(parsed, dict):
                lenses.append(parsed)
    return lenses


def _load_file(
    config_path: Path | None,
    config_stream: IO[str] | None,
) -> dict[str, Any]:
    """Parse YAML from a path or stream; return an empty dict when absent."""
    raw: str | None = None

    if config_stream is not None:
        raw = config_stream.read()
    elif config_path is not None and config_path.exists():
        raw = config_path.read_text()

    if not raw or not raw.strip():
        return {}

    parsed = yaml.safe_load(raw)
    if not isinstance(parsed, dict):
        return {}

    return parsed
