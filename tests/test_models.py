"""Contracts: round-trip serialise/deserialise + committed schema snapshots."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from lgtmaybe.core.models import (
    CustomLens,
    PRContext,
    Provider,
    ProviderResult,
    ReviewConfig,
    ReviewFinding,
    Severity,
)

SNAP_DIR = Path(__file__).parent / "snapshots"

SAMPLES: list[BaseModel] = [
    ReviewFinding(
        path="src/app.py",
        line=42,
        side="RIGHT",
        severity=Severity.high,
        title="possible NPE",
        body="`user` may be None here.",
        suggestion="if user is not None:",
    ),
    ProviderResult(text="hi", input_tokens=12, output_tokens=8),
    PRContext(
        diff="@@ -1 +1 @@\n-a\n+b\n",
        changed_files=["src/app.py"],
        base_sha="abc",
        head_sha="def",
        repo="lgtmaybe/lgtmaybe",
        pr_number=7,
    ),
    ReviewConfig(provider=Provider.bedrock, model="anthropic.claude-3-5-sonnet"),
]


@pytest.mark.parametrize("obj", SAMPLES, ids=lambda o: type(o).__name__)
def test_roundtrip(obj: BaseModel) -> None:
    restored = type(obj).model_validate_json(obj.model_dump_json())
    assert restored == obj


CONTRACT_MODELS = [ReviewFinding, ProviderResult, PRContext, ReviewConfig]


@pytest.mark.parametrize("model", CONTRACT_MODELS, ids=lambda m: m.__name__)
def test_schema_snapshot(model: type[BaseModel]) -> None:
    snap = SNAP_DIR / f"{model.__name__}.json"
    actual = json.dumps(model.model_json_schema(), indent=2, sort_keys=True)
    assert snap.exists(), f"missing committed snapshot: {snap}"
    assert actual == snap.read_text().rstrip("\n"), (
        f"schema for {model.__name__} drifted from committed snapshot"
    )


def test_severity_is_ordered() -> None:
    assert Severity.critical >= Severity.info
    assert Severity.high >= Severity.medium
    assert not (Severity.low >= Severity.high)


def test_review_config_accepts_api_base() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", api_base="http://localhost:11434")
    assert cfg.api_base == "http://localhost:11434"
    restored = ReviewConfig.model_validate_json(cfg.model_dump_json())
    assert restored.api_base == "http://localhost:11434"


def test_review_config_api_base_defaults_to_none() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.api_base is None


def test_review_config_timeout_defaults_to_none_auto() -> None:
    # None means "auto" — the factory resolves a provider-aware default.
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.timeout is None


def test_review_config_accepts_timeout() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", timeout=600)
    assert cfg.timeout == 600


def test_review_config_structured_output_defaults_true() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.structured_output is True


def test_review_config_temperature_defaults_to_zero() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.temperature == 0.0


def test_review_config_accepts_temperature() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", temperature=0.7)
    assert cfg.temperature == 0.7


def test_custom_lens_minimal_fields() -> None:
    lens = CustomLens(id="simplify", instructions="Flag needless code.")
    assert lens.id == "simplify"
    assert lens.title == ""
    assert lens.example_diff is None and lens.example_finding is None


def test_custom_lens_id_must_not_collide_with_builtin() -> None:
    with pytest.raises(ValidationError):
        CustomLens(id="security", instructions="x")


def test_custom_lens_id_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        CustomLens(id="   ", instructions="x")


def test_custom_lens_example_diff_and_finding_must_be_paired() -> None:
    finding = ReviewFinding(path="x.py", line=1, severity=Severity.low, title="t", body="b")
    with pytest.raises(ValidationError):
        CustomLens(id="simplify", instructions="x", example_diff="@@ -1 +1 @@\n+x\n")
    with pytest.raises(ValidationError):
        CustomLens(id="simplify", instructions="x", example_finding=finding)
    # Both together is valid.
    lens = CustomLens(
        id="simplify", instructions="x", example_diff="@@ -1 +1 @@\n+x\n", example_finding=finding
    )
    assert lens.example_finding is not None


def test_review_config_accepts_extra_lenses() -> None:
    cfg = ReviewConfig(
        provider=Provider.ollama,
        model="llama3",
        extra_lenses=[{"id": "simplify", "instructions": "Flag needless code."}],
    )
    assert cfg.extra_lenses[0].id == "simplify"
    restored = ReviewConfig.model_validate_json(cfg.model_dump_json())
    assert restored.extra_lenses[0].id == "simplify"


def test_review_config_extra_lens_ids_must_be_unique() -> None:
    with pytest.raises(ValidationError):
        ReviewConfig(
            provider=Provider.ollama,
            model="llama3",
            extra_lenses=[
                {"id": "simplify", "instructions": "a"},
                {"id": "simplify", "instructions": "b"},
            ],
        )


def test_review_config_extra_lenses_defaults_empty() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.extra_lenses == []


def test_review_config_reflect_defaults_to_true() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.reflect is True


def test_review_config_accepts_reflect_false() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", reflect=False)
    assert cfg.reflect is False


def test_review_config_resolve_fixed_defaults_to_true() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.resolve_fixed is True


def test_review_config_accepts_resolve_fixed_false() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", resolve_fixed=False)
    assert cfg.resolve_fixed is False


def test_pr_context_intent_fields_default_empty() -> None:
    """Stated-intent fields (title/description/commit messages) are optional so
    existing gateways and fixtures keep working; empty intent skips the lens."""
    ctx = PRContext(diff="", changed_files=[], base_sha="a", head_sha="b", repo="r", pr_number=1)
    assert ctx.title == ""
    assert ctx.description == ""
    assert ctx.commit_messages == []


def test_pr_context_carries_stated_intent() -> None:
    ctx = PRContext(
        diff="",
        changed_files=[],
        base_sha="a",
        head_sha="b",
        repo="r",
        pr_number=1,
        title="Add rate limiting",
        description="Limits login attempts.",
        commit_messages=["feat: add rate limiting"],
    )
    restored = PRContext.model_validate_json(ctx.model_dump_json())
    assert restored.title == "Add rate limiting"
    assert restored.commit_messages == ["feat: add rate limiting"]


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValueError):
        ProviderResult.model_validate(
            {
                "text": "x",
                "input_tokens": 1,
                "output_tokens": 1,
                "bogus": True,
            }
        )
