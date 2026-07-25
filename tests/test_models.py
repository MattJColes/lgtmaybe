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


def test_severity_is_totally_ordered() -> None:
    """All four comparisons rank by severity, never alphabetically (a StrEnum
    would otherwise fall back to string order, where "critical" < "high")."""
    assert Severity.critical > Severity.high
    assert Severity.info < Severity.low
    assert Severity.medium <= Severity.medium
    assert not (Severity.high < Severity.medium)
    assert sorted([Severity.high, Severity.info, Severity.critical, Severity.medium]) == [
        Severity.info,
        Severity.medium,
        Severity.high,
        Severity.critical,
    ]


def test_review_finding_severity_is_case_insensitive() -> None:
    """Models emit "High"/"MEDIUM"; the finding coerces case rather than failing."""
    cases = [("High", Severity.high), ("MEDIUM", Severity.medium), (" low ", Severity.low)]
    for raw, expected in cases:
        finding = ReviewFinding(path="x.py", line=1, severity=raw, title="t", body="b")
        assert finding.severity is expected


def test_review_finding_failure_scenario_defaults_to_none() -> None:
    finding = ReviewFinding(path="x.py", line=1, severity=Severity.high, title="bug", body="broken")

    assert finding.failure_scenario is None


def test_review_finding_failure_scenario_round_trips() -> None:
    scenario = "When the lookup misses, the new dereference raises AttributeError."
    finding = ReviewFinding(
        path="x.py",
        line=1,
        severity=Severity.low,
        title="unchecked lookup",
        body="The lookup result can be None.",
        failure_scenario=scenario,
    )

    restored = ReviewFinding.model_validate_json(finding.model_dump_json())

    assert restored.failure_scenario == scenario


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


def test_review_config_recursive_defaults_true() -> None:
    # RLM-style hunk walk for over-budget files is active by default.
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.recursive is True


def test_review_config_accepts_recursive_false() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", recursive=False)
    assert cfg.recursive is False


def test_review_config_symbol_resolution_defaults_true() -> None:
    # ast-grep cross-file symbol resolution during reflection is on by default.
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.symbol_resolution is True


def test_review_config_accepts_symbol_resolution_false() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", symbol_resolution=False)
    assert cfg.symbol_resolution is False


def test_review_config_structured_output_defaults_true() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.structured_output is True


def test_review_config_min_severity_defaults_to_low() -> None:
    # The floor sits at `low`, not `info`: pure-info narration ("X was removed")
    # is the weak-model noise tier and is dropped by default.
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.min_severity is Severity.low


def test_review_config_unanchored_min_severity_defaults_to_high() -> None:
    # A failed anchor is a low-confidence signal: only a high/critical guess is
    # worth surfacing without a precise line.
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.unanchored_min_severity is Severity.high


def test_review_config_fail_on_defaults_none() -> None:
    # The merge-gate is off by default (non-breaking): no check run is created.
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.fail_on is None


def test_review_config_accepts_fail_on() -> None:
    # A plain severity string coerces to the ordered Severity enum, so the gate
    # can compare `finding.severity >= cfg.fail_on`.
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", fail_on="high")
    restored = ReviewConfig.model_validate_json(cfg.model_dump_json())
    assert restored.fail_on is Severity.high


def test_review_finding_broad_defaults_false() -> None:
    # `broad` is engine-set (like `anchored`); a fresh finding is not broad.
    f = ReviewFinding(path="x.py", line=1, severity=Severity.high, title="t", body="b")
    assert f.broad is False
    broad = f.model_copy(update={"broad": True})
    assert ReviewFinding.model_validate_json(broad.model_dump_json()).broad is True


def test_review_config_reflect_model_defaults_none() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.reflect_model is None


def test_review_config_accepts_reflect_model() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", reflect_model="big-judge")
    restored = ReviewConfig.model_validate_json(cfg.model_dump_json())
    assert restored.reflect_model == "big-judge"


def test_review_config_language_defaults_none() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.language is None


def test_review_config_accepts_language() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", language="Japanese")
    restored = ReviewConfig.model_validate_json(cfg.model_dump_json())
    assert restored.language == "Japanese"


def test_review_config_ignore_fingerprints_defaults_empty() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.ignore_fingerprints == []


def test_review_config_accepts_ignore_fingerprints() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", ignore_fingerprints=["abc123"])
    restored = ReviewConfig.model_validate_json(cfg.model_dump_json())
    assert restored.ignore_fingerprints == ["abc123"]


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


def test_review_config_answer_replies_defaults_to_true() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert cfg.answer_replies is True


def test_review_config_accepts_answer_replies_false() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", answer_replies=False)
    assert cfg.answer_replies is False
    restored = ReviewConfig.model_validate_json(cfg.model_dump_json())
    assert restored.answer_replies is False


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


def test_review_finding_accepts_anchor() -> None:
    f = ReviewFinding(
        path="a.py",
        line=42,
        severity=Severity.high,
        title="t",
        body="b",
        anchor="    return x",
    )
    assert f.anchor == "    return x"
    restored = ReviewFinding.model_validate_json(f.model_dump_json())
    assert restored.anchor == "    return x"


def test_review_finding_anchor_defaults_to_none() -> None:
    f = ReviewFinding(path="a.py", line=1, severity=Severity.low, title="t", body="b")
    assert f.anchor is None


def test_review_finding_anchored_defaults_true() -> None:
    f = ReviewFinding(path="a.py", line=1, severity=Severity.low, title="t", body="b")
    assert f.anchored is True
    restored = ReviewFinding.model_validate_json(
        f.model_copy(update={"anchored": False}).model_dump_json()
    )
    assert restored.anchored is False


def test_review_finding_line_must_be_positive() -> None:
    """Line numbers are 1-based; a 0/negative line is bogus model output and would
    map to no real diff line (or worse, a wrong one) — reject it at the boundary."""
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            ReviewFinding(path="a.py", line=bad, severity=Severity.low, title="t", body="b")
    # The first valid line is accepted.
    f = ReviewFinding(path="a.py", line=1, severity=Severity.low, title="t", body="b")
    assert f.line == 1
