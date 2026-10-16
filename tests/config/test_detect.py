"""What a first-run `config init` can detect, without asking.

Every probe is injected or fed a mock transport, so nothing here touches the
network, a real cloud SDK, or the developer's own ~/.aws / gcloud files.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from lgtmaybe.config import detect
from lgtmaybe.core.models import Provider

_DOCS = Path(__file__).resolve().parents[2] / "docs"


def _no_ollama(_base: str) -> None:
    return None


def _detect(env: dict[str, str], **kwargs: object) -> list[detect.Detection]:
    defaults: dict[str, object] = {
        "env": env,
        "probe_ollama": _no_ollama,
        "aws_probe": lambda _env: False,
        "gcp_probe": lambda: False,
        "chatgpt_probe": lambda _env: False,
    }
    defaults.update(kwargs)
    return detect.detect(**defaults)  # type: ignore[arg-type]


def test_nothing_detected_on_a_bare_machine() -> None:
    assert _detect({}) == []


@pytest.mark.parametrize(
    ("var", "provider"),
    [
        ("ANTHROPIC_API_KEY", Provider.anthropic),
        ("OPENAI_API_KEY", Provider.openai),
        ("OPENROUTER_API_KEY", Provider.openrouter),
        ("ZAI_API_KEY", Provider.zai),
    ],
)
def test_provider_api_key_in_env_is_detected(var: str, provider: Provider) -> None:
    found = _detect({var: "secret-value"})

    assert [d.provider for d in found] == [provider]
    assert var in found[0].reason
    # The key itself is never echoed back, only its variable name.
    assert "secret-value" not in found[0].reason


def test_blank_api_key_is_not_a_detection() -> None:
    assert _detect({"ANTHROPIC_API_KEY": ""}) == []


def test_running_ollama_is_detected_with_its_pulled_models() -> None:
    found = _detect({}, probe_ollama=lambda _base: ("qwen3.6:27b", "gemma4:e4b"))

    assert [d.provider for d in found] == [Provider.ollama]
    assert found[0].models == ("qwen3.6:27b", "gemma4:e4b")
    assert "ollama" in found[0].reason
    assert "2 models" in found[0].reason


def test_azure_endpoint_in_env_is_detected_with_its_base() -> None:
    found = _detect({"AZURE_API_BASE": "https://res.openai.azure.com"})

    assert [d.provider for d in found] == [Provider.azure]
    assert found[0].api_base == "https://res.openai.azure.com"


def test_ambient_cloud_credentials_are_detected() -> None:
    found = _detect({}, aws_probe=lambda _env: True, gcp_probe=lambda: True)

    assert [d.provider for d in found] == [Provider.bedrock, Provider.vertex]


def test_a_chatgpt_sign_in_is_detected() -> None:
    found = _detect({}, chatgpt_probe=lambda _env: True)

    assert [d.provider for d in found] == [Provider.chatgpt]
    assert "lgtmaybe login chatgpt" in found[0].reason


def test_the_default_chatgpt_probe_reads_the_token_dir_from_env(tmp_path: Path) -> None:
    env = {"CHATGPT_TOKEN_DIR": str(tmp_path)}
    found = detect.detect(
        env=env, probe_ollama=_no_ollama, aws_probe=lambda _env: False, gcp_probe=lambda: False
    )
    assert found == []

    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    found = detect.detect(
        env=env, probe_ollama=_no_ollama, aws_probe=lambda _env: False, gcp_probe=lambda: False
    )
    assert [d.provider for d in found] == [Provider.chatgpt]


def test_explicit_keys_come_before_local_and_ambient_detections() -> None:
    """A key the user exported is the strongest signal; ambient cloud files are
    the weakest (an ~/.aws for another project says little about Bedrock)."""
    found = _detect(
        {"OPENAI_API_KEY": "k", "AZURE_API_BASE": "https://r"},
        probe_ollama=lambda _base: (),
        aws_probe=lambda _env: True,
        gcp_probe=lambda: True,
        chatgpt_probe=lambda _env: True,
    )

    assert [d.provider for d in found] == [
        Provider.openai,
        Provider.chatgpt,
        Provider.ollama,
        Provider.azure,
        Provider.bedrock,
        Provider.vertex,
    ]


class TestAwsProbe:
    def test_env_credentials(self, tmp_path: Path) -> None:
        assert detect.aws_credentials_present({"AWS_PROFILE": "dev"}, home=tmp_path)

    def test_shared_files(self, tmp_path: Path) -> None:
        (tmp_path / ".aws").mkdir()
        (tmp_path / ".aws" / "credentials").write_text("[default]\n")

        assert detect.aws_credentials_present({}, home=tmp_path)

    def test_nothing(self, tmp_path: Path) -> None:
        assert not detect.aws_credentials_present({}, home=tmp_path)


class TestOllamaProbe:
    def _transport(self, handler) -> httpx.MockTransport:
        return httpx.MockTransport(handler)

    def test_lists_pulled_models(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(
                200, json={"models": [{"name": "qwen3.6:27b"}, {"name": "gemma4:e4b"}]}
            )

        models = detect.ollama_models("http://localhost:11434/", transport=self._transport(handler))

        assert models == ("qwen3.6:27b", "gemma4:e4b")
        assert seen == ["http://localhost:11434/api/tags"]

    def test_unreachable_is_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        assert detect.ollama_models("http://x", transport=self._transport(handler)) is None

    def test_error_status_is_none(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        assert detect.ollama_models("http://x", transport=self._transport(handler)) is None

    def test_unexpected_body_is_running_with_no_models(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"models": "nope"})

        assert detect.ollama_models("http://x", transport=self._transport(handler)) == ()


class TestSuggestedModel:
    def test_every_provider_has_an_entry(self) -> None:
        assert set(detect.SUGGESTED_MODELS) == set(Provider)

    def test_suggestions_match_the_docs(self) -> None:
        """The table follows the docs' "model IDs kept current" rule: every
        suggestion is a model ID the how-to guides already recommend."""
        docs = "\n".join(p.read_text(encoding="utf-8") for p in (_DOCS / "how-to").glob("*.md"))
        for provider, model in detect.SUGGESTED_MODELS.items():
            if model is not None:
                assert f"`{model}`" in docs or f"--model {model}" in docs, (provider, model)

    def test_deployment_named_providers_have_no_guess(self) -> None:
        """Azure and openai-compatible models are names the user chose."""
        assert detect.SUGGESTED_MODELS[Provider.azure] is None
        assert detect.SUGGESTED_MODELS[Provider.openai_compatible] is None

    def test_ollama_prefers_the_suggestion_when_pulled(self) -> None:
        pulled = ("gemma4:e4b", detect.SUGGESTED_MODELS[Provider.ollama] or "")
        assert detect.suggest_model(Provider.ollama, pulled) == pulled[1]

    def test_ollama_falls_back_to_a_pulled_model(self) -> None:
        assert detect.suggest_model(Provider.ollama, ("gemma4:e4b",)) == "gemma4:e4b"

    def test_hosted_provider_uses_the_table(self) -> None:
        assert detect.suggest_model(Provider.anthropic) == "claude-sonnet-4-6"
