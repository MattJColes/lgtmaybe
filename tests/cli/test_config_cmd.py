"""The `config` command group: set/get/show/path + init wizard."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from lgtmaybe.cli import main


@pytest.fixture
def cfg_home(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_set_then_show(cfg_home: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(main, ["config", "set", "model", "qwen3:27b"]).exit_code == 0

    result = runner.invoke(main, ["config", "show"])
    assert result.exit_code == 0
    assert "qwen3:27b" in result.output


def test_get_prints_value(cfg_home: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["config", "set", "provider", "ollama"])

    result = runner.invoke(main, ["config", "get", "provider"])
    assert result.exit_code == 0
    assert result.output.strip() == "ollama"


def test_path_prints_location(cfg_home: Path) -> None:
    result = CliRunner().invoke(main, ["config", "path"])
    assert result.exit_code == 0
    assert str(cfg_home / "lgtmaybe" / "config.yml") in result.output


def test_set_api_key_is_refused(cfg_home: Path) -> None:
    result = CliRunner().invoke(main, ["config", "set", "api_key", "sk-secret"])
    assert result.exit_code != 0
    assert "environment" in result.output


def test_set_unknown_key_is_refused(cfg_home: Path) -> None:
    result = CliRunner().invoke(main, ["config", "set", "nope", "x"])
    assert result.exit_code != 0
    assert "unknown config key" in result.output


def test_get_unknown_key_is_refused(cfg_home: Path) -> None:
    result = CliRunner().invoke(main, ["config", "get", "nope"])
    assert result.exit_code != 0
    assert "valid keys:" in result.output


def test_init_wizard_writes_file(cfg_home: Path) -> None:
    result = CliRunner().invoke(
        main, ["config", "init"], input="ollama\nqwen3:27b\nhttp://localhost:11434\n"
    )
    assert result.exit_code == 0, result.output

    from lgtmaybe.config import store

    assert store.get_key("provider") == "ollama"
    assert store.get_key("model") == "qwen3:27b"
    assert store.get_key("api_base") == "http://localhost:11434"
