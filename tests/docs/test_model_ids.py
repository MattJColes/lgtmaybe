"""Every hosted model id the docs and examples name resolves in litellm's model map.

The adapter sizes its input budget (``_fit_input_budget``) and detects caching
and schema support from litellm's map, so an id the map doesn't know runs with
no window and no capabilities — and a copy-pasted example that 404s is the
first thing a new user hits. A litellm bump that drops an entry should fail
here, not in someone's first review.

The check is an exact key lookup, deliberately stricter than
``litellm.get_model_info``: litellm's fallback generalisations answer for any
``claude-*`` or ``gemini-*`` shaped id, so a removed or never-real id would
still "resolve" through it with a guessed window.

Providers whose model name is the user's own choice are out of scope: ollama
(whatever was pulled), openai-compatible (whatever the server serves) and azure
(a deployment name, not a model id).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import litellm
import pytest
import yaml

from lgtmaybe.core.models import Provider
from lgtmaybe.providers.factory import litellm_model_string

_ROOT = Path(__file__).parents[2]

_USER_NAMED = frozenset({Provider.ollama, Provider.openai_compatible, Provider.azure})
_MODEL_KEYS = ("model", "fallback_model", "triage_model", "reflect_model")
_MODEL_FLAGS = ("--model", "--fallback-model", "--triage-model", "--reflect-model")

_FENCE = re.compile(r"^```(\w*)[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


def _sources() -> list[Path]:
    paths = [
        *sorted((_ROOT / "examples").rglob("*.yml")),
        _ROOT / "README.md",
        _ROOT / "DEVELOPMENT.md",
        *sorted((_ROOT / "docs").rglob("*.md")),
    ]
    return [p for p in paths if p.is_file()]


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _from_yaml(text: str) -> Iterator[tuple[str, str]]:
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        return
    for mapping in _walk(docs):
        provider = mapping.get("provider")
        if not isinstance(provider, str):
            continue
        for key in _MODEL_KEYS:
            model = mapping.get(key)
            if isinstance(model, str):
                yield provider, model


def _from_shell(text: str) -> Iterator[tuple[str, str]]:
    for command in text.replace("\\\n", " ").splitlines():
        provider = re.search(r"--provider[ =](\S+)", command)
        if provider is None:
            continue
        for flag in _MODEL_FLAGS:
            for model in re.findall(rf"{flag}[ =](\S+)", command):
                yield provider.group(1), model


def _table_ids(text: str, heading: str) -> Iterator[str]:
    """Backticked ids in the table under *heading* (one Markdown section)."""
    assert heading in text, f"model table heading {heading!r} not found; update this test"
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    for row in section.splitlines():
        if row.startswith("|"):
            yield from re.findall(r"`([^`\s]+)`", row)


def _named_models() -> list[tuple[str, Provider, str]]:
    found: set[tuple[str, Provider, str]] = set()
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        where = path.relative_to(_ROOT).as_posix()
        pairs: list[tuple[str, str]] = []
        if path.suffix == ".yml":
            pairs += _from_yaml(text)
            pairs += _from_shell(text)
        else:
            for lang, body in _FENCE.findall(text):
                if lang in {"yaml", "yml"}:
                    pairs += _from_yaml(body)
                elif lang in {"bash", "sh", "shell", "console", ""}:
                    pairs += _from_shell(body)
        for provider_name, model in pairs:
            try:
                provider = Provider(provider_name)
            except ValueError:
                continue  # a templated or illustrative value, not a provider
            if provider in _USER_NAMED or "${{" in model or "<" in model:
                continue
            found.add((where, provider, model))

    bedrock = (_ROOT / "docs/how-to/review-with-bedrock-oidc.md").read_text(encoding="utf-8")
    for model in _table_ids(bedrock, "## Choosing a Bedrock model ID"):
        found.add(("docs/how-to/review-with-bedrock-oidc.md (table)", Provider.bedrock, model))
    vertex = (_ROOT / "docs/how-to/review-with-vertex-wif.md").read_text(encoding="utf-8")
    for model in _table_ids(vertex, "## Available Vertex AI models"):
        found.add(("docs/how-to/review-with-vertex-wif.md (table)", Provider.vertex, model))
    return sorted(found, key=lambda item: (item[0], item[1].value, item[2]))


def _in_model_map(provider: Provider, model: str) -> bool:
    """The routed key, or the bare key when the bare entry belongs to the same
    provider — litellm's own lookup strips the route prefix but checks the
    entry's ``litellm_provider``, so a bare key owned by another provider (a
    vertex id answered by anthropic's entry) would resolve here and not there.
    """
    routed = litellm_model_string(provider, model)
    if routed in litellm.model_cost:
        return True
    entry = litellm.model_cost.get(model)
    owner = entry.get("litellm_provider") if isinstance(entry, dict) else None
    route = routed.split("/", 1)[0]
    return isinstance(owner, str) and (
        owner == route or owner.startswith((f"{route}_", f"{route}-"))
    )


_NAMED = _named_models()


def test_the_scan_finds_the_documented_models() -> None:
    """Guard the scanner itself: a parsing regression must not pass vacuously."""
    providers = {provider for _, provider, _ in _NAMED}
    assert len(_NAMED) >= 30
    assert providers >= {
        Provider.openai,
        Provider.anthropic,
        Provider.openrouter,
        Provider.bedrock,
        Provider.vertex,
        Provider.zai,
    }


@pytest.mark.parametrize(
    ("where", "provider", "model"),
    _NAMED,
    ids=[f"{where}:{provider.value}:{model}" for where, provider, model in _NAMED],
)
def test_documented_model_id_is_in_litellm_map(where: str, provider: Provider, model: str) -> None:
    assert _in_model_map(provider, model), (
        f"{where} names --provider {provider.value} --model {model}, which litellm "
        f"{litellm_model_string(provider, model)!r} does not map. Update the doc to an id "
        "litellm knows (litellm-native form for that platform)."
    )


def test_a_bare_key_owned_by_another_provider_does_not_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(litellm.model_cost, "claude-test-only", {"litellm_provider": "anthropic"})

    assert _in_model_map(Provider.anthropic, "claude-test-only")
    assert not _in_model_map(Provider.vertex, "claude-test-only")
