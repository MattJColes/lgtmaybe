"""User-level config store: set/get round-trips, coercion, and refusals."""

from __future__ import annotations

from pathlib import Path

import pytest

from lgtmaybe.config import store


@pytest.fixture
def cfg_home(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_user_config_path_honours_xdg(cfg_home: Path) -> None:
    assert store.user_config_path() == cfg_home / "lgtmaybe" / "config.yml"


def test_set_then_get_round_trips(cfg_home: Path) -> None:
    store.set_key("provider", "ollama")
    store.set_key("model", "qwen3:27b")

    assert store.get_key("provider") == "ollama"
    assert store.get_key("model") == "qwen3:27b"


def test_set_coerces_to_field_type(cfg_home: Path) -> None:
    store.set_key("max_files", "25")

    assert store.get_key("max_files") == 25  # int, not "25"


def test_set_fail_on_round_trips(cfg_home: Path) -> None:
    # A nullable-Severity field coerces the string to the enum's plain value.
    store.set_key("fail_on", "high")

    assert store.get_key("fail_on") == "high"


def test_language_round_trips(cfg_home: Path) -> None:
    store.set_key("language", "Japanese")

    assert store.get_key("language") == "Japanese"


def test_set_validates_enum_value(cfg_home: Path) -> None:
    with pytest.raises(ValueError):
        store.set_key("provider", "not-a-provider")


def test_set_rejects_api_key_with_guidance(cfg_home: Path) -> None:
    with pytest.raises(ValueError, match="environment"):
        store.set_key("api_key", "sk-secret")


def test_set_rejects_unknown_key(cfg_home: Path) -> None:
    with pytest.raises(ValueError, match="unknown config key"):
        store.set_key("favourite_colour", "blue")


def test_load_absent_file_is_empty(cfg_home: Path) -> None:
    assert store.load() == {}
