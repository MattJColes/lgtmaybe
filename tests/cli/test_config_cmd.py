"""The `config` command group: set/get/show/path + init wizard."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

import lgtmaybe.cli as cli_module
from lgtmaybe.cli import main
from lgtmaybe.config import detect
from lgtmaybe.core.models import Provider, ProviderResult


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


# --- `config init`: detect, offer, suggest, verify, write -------------------
#
# Detection and the model call are both replaced: `detect.detect` /
# `detect.ollama_models` with canned answers, and the provider FACTORY with a
# fake client, so the real credential resolver still runs (its "how to auth"
# message is the one init must show) but nothing reaches the network.


class _Client:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[str] = []

    def complete(self, messages: list[dict[str, str]], model: str, **_opts: object) -> object:
        self.calls.append(model)
        if self.error is not None:
            raise self.error
        return ProviderResult(text="ok", input_tokens=5, output_tokens=1)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> _Client:
    fake = _Client()
    monkeypatch.setattr(cli_module, "build_provider", lambda *_a, **_k: fake)
    return fake


def _detected(monkeypatch: pytest.MonkeyPatch, *found: detect.Detection) -> None:
    monkeypatch.setattr(detect, "detect", lambda: list(found))
    monkeypatch.setattr(detect, "ollama_models", lambda _base: None)


def _written() -> dict[str, object]:
    from lgtmaybe.config import store

    return store.load()


def test_init_with_running_ollama_takes_two_keypresses(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch, client: _Client
) -> None:
    """FEATURES.md P0-2 acceptance: confirm the provider, accept the model."""
    _detected(
        monkeypatch,
        detect.Detection(
            Provider.ollama,
            "ollama running at http://localhost:11434 (2 models pulled)",
            models=("gemma4:e4b", "qwen3.6:27b"),
            api_base="http://localhost:11434",
        ),
    )

    result = CliRunner().invoke(main, ["config", "init"], input="\n\n")

    assert result.exit_code == 0, result.output
    assert _written() == {"provider": "ollama", "model": "qwen3.6:27b"}
    assert client.calls == ["qwen3.6:27b"]
    assert str(cfg_home / "lgtmaybe" / "config.yml") in result.output
    assert result.output.strip().splitlines()[-1].endswith("lgtmaybe review")


def test_init_offers_a_detected_api_key_and_suggests_a_current_model(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch, client: _Client
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-value")
    _detected(monkeypatch, detect.Detection(Provider.anthropic, "ANTHROPIC_API_KEY"))

    result = CliRunner().invoke(main, ["config", "init"], input="\n\n")

    assert result.exit_code == 0, result.output
    assert "Found ANTHROPIC_API_KEY - use anthropic?" in result.output
    assert _written() == {"provider": "anthropic", "model": "claude-sonnet-4-6"}


def test_init_never_persists_the_api_key(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch, client: _Client
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-value")
    _detected(monkeypatch, detect.Detection(Provider.anthropic, "ANTHROPIC_API_KEY"))

    CliRunner().invoke(main, ["config", "init"], input="\n\n")

    text = (cfg_home / "lgtmaybe" / "config.yml").read_text(encoding="utf-8")
    assert "sk-ant-test-value" not in text
    assert "api_key" not in text


def test_init_wrong_key_fails_now_and_writes_nothing(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad key fails during init, not minutes into the first review."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-wrong")
    _detected(monkeypatch, detect.Detection(Provider.openai, "OPENAI_API_KEY"))
    failing = _Client(error=RuntimeError("AuthenticationError: Incorrect API key provided"))
    monkeypatch.setattr(cli_module, "build_provider", lambda *_a, **_k: failing)

    result = CliRunner().invoke(main, ["config", "init"], input="\n\n")

    assert result.exit_code != 0
    assert "Incorrect API key provided" in result.output
    assert "--no-verify" in result.output
    assert not (cfg_home / "lgtmaybe" / "config.yml").exists()


def test_init_missing_credentials_shows_the_resolvers_auth_message(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch, client: _Client
) -> None:
    """The "how to auth" text is the credential resolver's own, word for word,
    so init and review can never give different advice."""
    from lgtmaybe.providers.credentials import resolve_credentials

    with pytest.raises(ValueError) as expected:
        resolve_credentials(Provider.openai)
    _detected(monkeypatch)

    result = CliRunner().invoke(main, ["config", "init"], input="openai\n\n")

    assert result.exit_code != 0
    assert str(expected.value) in result.output.replace("\n", " ")
    assert client.calls == []
    assert not (cfg_home / "lgtmaybe" / "config.yml").exists()


def test_init_no_verify_writes_without_a_model_call(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("--no-verify must not build a provider")

    monkeypatch.setattr(cli_module, "build_provider", _refuse)
    _detected(monkeypatch)

    result = CliRunner().invoke(main, ["config", "init", "--no-verify"], input="openai\ngpt-5.5\n")

    assert result.exit_code == 0, result.output
    assert _written() == {"provider": "openai", "model": "gpt-5.5"}


def test_init_rejects_an_unknown_provider_and_asks_again(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch, client: _Client
) -> None:
    _detected(monkeypatch)

    result = CliRunner().invoke(main, ["config", "init", "--no-verify"], input="llama\nopenai\n\n")

    assert result.exit_code == 0, result.output
    assert "unknown provider 'llama'" in result.output
    assert _written()["provider"] == "openai"


def test_init_accepts_a_provider_by_number(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch, client: _Client
) -> None:
    _detected(monkeypatch)
    number = list(Provider).index(Provider.vertex) + 1

    result = CliRunner().invoke(main, ["config", "init", "--no-verify"], input=f"{number}\n\n")

    assert result.exit_code == 0, result.output
    assert f"{number}. vertex" in result.output
    assert _written() == {"provider": "vertex", "model": "gemini-3.8-flash"}


def test_init_declining_the_detection_offers_the_full_list(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch, client: _Client
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    _detected(monkeypatch, detect.Detection(Provider.openai, "OPENAI_API_KEY"))

    result = CliRunner().invoke(main, ["config", "init", "--no-verify"], input="n\nopenrouter\n\n")

    assert result.exit_code == 0, result.output
    assert _written()["provider"] == "openrouter"


def test_init_wizard_writes_file(cfg_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An undetected ollama on another host: the base is asked for and kept."""
    _detected(monkeypatch)
    probed: list[str] = []

    def _models(base: str) -> tuple[str, ...]:
        probed.append(base)
        return ("qwen3:27b",)

    monkeypatch.setattr(detect, "ollama_models", _models)

    result = CliRunner().invoke(
        main, ["config", "init", "--no-verify"], input="ollama\nhttp://gpu-box:11434\n\n"
    )
    assert result.exit_code == 0, result.output

    from lgtmaybe.config import store

    assert probed == ["http://gpu-box:11434"]
    assert store.get_key("provider") == "ollama"
    assert store.get_key("model") == "qwen3:27b"
    assert store.get_key("api_base") == "http://gpu-box:11434"


def test_init_azure_asks_for_the_endpoint_and_deployment(
    cfg_home: Path, monkeypatch: pytest.MonkeyPatch, client: _Client
) -> None:
    """No model is guessed for a provider whose model is a name the user chose."""
    _detected(monkeypatch)

    result = CliRunner().invoke(
        main,
        ["config", "init", "--no-verify"],
        input="azure\nhttps://res.openai.azure.com\nmy-deployment\n",
    )

    assert result.exit_code == 0, result.output
    assert _written() == {
        "provider": "azure",
        "model": "my-deployment",
        "api_base": "https://res.openai.azure.com",
    }
