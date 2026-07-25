"""User-level config persistence for the local CLI.

Stores non-secret review settings (provider, model, api_base, severity floor,
caps) in a single YAML file so they can be set once and reused across repos.
API keys are deliberately never persisted — they stay in the environment.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter

from lgtmaybe.core.models import ReviewConfig

# Keys we refuse to store, with a clear nudge toward the environment instead.
_SECRET_KEYS = {"api_key", "api_token", "token"}


def user_config_path() -> Path:
    """The user-level config location (honours $XDG_CONFIG_HOME)."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "lgtmaybe" / "config.yml"


def load(path: Path | None = None) -> dict[str, Any]:
    """Return the config dict, or empty when the file is absent/blank."""
    path = path or user_config_path()
    if not path.exists():
        return {}
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def save(data: dict[str, Any], path: Path | None = None) -> None:
    """Write the config dict as YAML, creating parent directories as needed."""
    path = path or user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")


def as_yaml(path: Path | None = None) -> str:
    """Return the stored config rendered as YAML text ("" when empty)."""
    data = load(path)
    return yaml.safe_dump(data, sort_keys=True).rstrip() if data else ""


def get_key(key: str, path: Path | None = None) -> Any:
    """Return one stored value, or None when unset."""
    return load(path).get(key)


def set_key(key: str, value: str, path: Path | None = None) -> Any:
    """Validate, type-coerce, and persist a single config value; return it."""
    _validate_key(key)
    data = load(path)
    data[key] = _coerce(key, value)
    save(data, path)
    return data[key]


def _validate_key(key: str) -> None:
    if key in _SECRET_KEYS:
        raise ValueError(
            "API keys are read from the environment, not stored in config. "
            "Set the provider's env var (e.g. OPENAI_API_KEY) or pass --api-key."
        )
    if key not in ReviewConfig.model_fields:
        valid = ", ".join(sorted(ReviewConfig.model_fields))
        raise ValueError(f"unknown config key {key!r}; valid keys: {valid}")


def _coerce(key: str, value: str) -> Any:
    """Coerce a string value to the field's type, storing enums as plain values."""
    annotation = ReviewConfig.model_fields[key].annotation
    coerced: Any = TypeAdapter(annotation).validate_strings(value)
    return coerced.value if isinstance(coerced, Enum) else coerced
