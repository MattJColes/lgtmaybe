"""ReviewConfig's grouped settings keep every flat key working (#836).

`batching`, `limits` and `request` gather the settings one stage reads, so a
reader names the group it depends on. Config files, the user store, CLI flags,
Action inputs and keyword construction all still speak the flat keys: a
before-validator lifts each into its group. A key set both ways is an error,
an unknown key fails at either level, and "configured" still means configured
once the field lives in a group.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lgtmaybe.config import store
from lgtmaybe.config.loader import load_config
from lgtmaybe.core.models import (
    BatchingConfig,
    LimitsConfig,
    Provider,
    RequestConfig,
    ReviewConfig,
    Severity,
)

_FLAT_YAML = """\
provider: ollama
model: qwen3:8b
include_paths: ["src/**"]
exclude_paths: ["src/vendor/**"]
max_files: 12
max_file_diff_lines: 900
max_input_tokens: 40000
recursive: false
context_lines: 5
function_context: false
max_review_seconds: 600
max_review_tokens: 250000
max_findings_per_lens: 9
max_concurrency: 2
timeout: 90
temperature: 0.2
max_tokens: 8192
reasoning_effort: low
num_ctx: 65536
min_severity: medium
"""

_NESTED_YAML = """\
provider: ollama
model: qwen3:8b
batching:
  include_paths: ["src/**"]
  exclude_paths: ["src/vendor/**"]
  max_files: 12
  max_file_diff_lines: 900
  max_input_tokens: 40000
  recursive: false
  context_lines: 5
  function_context: false
limits:
  max_review_seconds: 600
  max_review_tokens: 250000
  max_findings_per_lens: 9
  max_concurrency: 2
request:
  timeout: 90
  temperature: 0.2
  max_tokens: 8192
  reasoning_effort: low
  num_ctx: 65536
min_severity: medium
"""


def _load(tmp_path: Path, text: str) -> ReviewConfig:
    path = tmp_path / ".lgtmaybe.yml"
    path.write_text(text, encoding="utf-8")
    return load_config(config_path=path)


def _assert_grouped(cfg: ReviewConfig) -> None:
    assert cfg.batching == BatchingConfig(
        include_paths=["src/**"],
        exclude_paths=["src/vendor/**"],
        max_files=12,
        max_file_diff_lines=900,
        max_input_tokens=40000,
        recursive=False,
        context_lines=5,
        function_context=False,
    )
    assert cfg.limits == LimitsConfig(
        max_review_seconds=600,
        max_review_tokens=250000,
        max_findings_per_lens=9,
        max_concurrency=2,
    )
    assert cfg.request == RequestConfig(
        timeout=90, temperature=0.2, max_tokens=8192, reasoning_effort="low", num_ctx=65536
    )
    assert cfg.min_severity is Severity.medium


def test_a_flat_config_file_loads_into_the_groups(tmp_path: Path) -> None:
    """Every existing .lgtmaybe.yml is flat; it must load unchanged."""
    _assert_grouped(_load(tmp_path, _FLAT_YAML))


def test_the_nested_form_loads_the_same_config(tmp_path: Path) -> None:
    _assert_grouped(_load(tmp_path, _NESTED_YAML))


def test_flat_and_nested_keys_of_one_group_merge() -> None:
    cfg = ReviewConfig.model_validate(
        {
            "provider": "ollama",
            "model": "m",
            "batching": {"max_files": 3},
            "context_lines": 0,
        }
    )
    assert cfg.batching.max_files == 3
    assert cfg.batching.context_lines == 0


def test_a_key_given_flat_and_nested_is_rejected_by_name() -> None:
    with pytest.raises(ValidationError, match=r"max_files.*batching"):
        ReviewConfig.model_validate(
            {"provider": "ollama", "model": "m", "max_files": 3, "batching": {"max_files": 4}}
        )


def test_keyword_construction_with_flat_keys_still_works() -> None:
    cfg = ReviewConfig(
        provider=Provider.ollama,
        model="m",
        max_input_tokens=5000,
        max_review_seconds=0,
        timeout=30,
    )
    assert cfg.batching.max_input_tokens == 5000
    assert cfg.limits.max_review_seconds == 0
    assert cfg.request.timeout == 30


def test_a_group_instance_and_a_flat_key_merge_keeping_what_was_set() -> None:
    cfg = ReviewConfig(
        provider=Provider.ollama,
        model="m",
        batching=BatchingConfig(max_files=7),
        context_lines=2,
    )
    assert (cfg.batching.max_files, cfg.batching.context_lines) == (7, 2)
    assert cfg.batching.model_fields_set == {"max_files", "context_lines"}


@pytest.mark.parametrize(
    "data",
    [
        {"max_filez": 3},
        {"batching": {"max_filez": 3}},
        {"limits": {"timeout": 30}},  # a real key, but of another group
    ],
    ids=["top-level", "inside-group", "wrong-group"],
)
def test_unknown_keys_fail_at_both_levels(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ReviewConfig.model_validate({"provider": "ollama", "model": "m", **data})


def test_the_timeout_guard_still_applies_through_a_flat_key() -> None:
    with pytest.raises(ValidationError, match="positive number of seconds"):
        ReviewConfig.model_validate({"provider": "ollama", "model": "m", "timeout": 0})


def test_defaults_stay_unset_and_configured_values_stay_set() -> None:
    """`_fit_input_budget` keys off the group's fields_set: a defaulted budget is
    fitted, a configured one (flat or nested) is the user's call. Setting a
    sibling key in the same group must not mark the budget configured."""
    base = {"provider": "ollama", "model": "m"}
    assert "max_input_tokens" not in ReviewConfig.model_validate(base).batching.model_fields_set
    sibling = ReviewConfig.model_validate({**base, "context_lines": 0})
    assert "max_input_tokens" not in sibling.batching.model_fields_set
    flat = ReviewConfig.model_validate({**base, "max_input_tokens": 9})
    nested = ReviewConfig.model_validate({**base, "batching": {"max_input_tokens": 9}})
    assert "max_input_tokens" in flat.batching.model_fields_set
    assert "max_input_tokens" in nested.batching.model_fields_set


def test_model_copy_refuses_a_flat_grouped_key() -> None:
    """model_copy skips validation, so a flat key would land as an orphan
    attribute the readers never see. Fail loudly instead."""
    cfg = ReviewConfig(provider=Provider.ollama, model="m")
    with pytest.raises(ValueError, match=r"max_input_tokens.*batching"):
        cfg.model_copy(update={"max_input_tokens": 1})


def test_grouped_settings_are_not_flat_attributes() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="m")
    assert not hasattr(cfg, "max_input_tokens")


def test_the_flat_key_view_lists_group_members_not_group_names() -> None:
    flat = ReviewConfig.flat_fields()
    assert {"max_input_tokens", "max_review_seconds", "timeout", "min_severity"} <= set(flat)
    assert not {"batching", "limits", "request"} & set(flat)
    # The nested sub-models that predate the groups keep their single key.
    assert {"static_analysis", "risk", "project"} <= set(flat)


def test_the_user_store_still_sets_and_coerces_a_grouped_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    store.set_key("max_input_tokens", "5000")
    store.set_key("timeout", "45")
    assert store.get_key("max_input_tokens") == 5000
    cfg = load_config(user_config_path=store.user_config_path(), provider="ollama", model="m")
    assert cfg.batching.max_input_tokens == 5000
    assert cfg.request.timeout == 45


def test_a_flat_cli_value_beats_a_flat_file_value(tmp_path: Path) -> None:
    """Layer precedence is unchanged for the flat form every surface uses."""
    path = tmp_path / ".lgtmaybe.yml"
    path.write_text("provider: ollama\nmodel: m\nmax_files: 12\n", encoding="utf-8")
    assert load_config(config_path=path, max_files=3).batching.max_files == 3


@pytest.mark.parametrize(
    "grouped",
    [{"max_input_tokens": 9}, {"batching": {"max_input_tokens": 9}}],
    ids=["flat", "nested"],
)
def test_the_prototype_stage_still_moves_only_a_defaulted_floor(
    grouped: dict[str, object],
) -> None:
    base = {"provider": "ollama", "model": "m", "project": {"stage": "prototype"}, **grouped}
    assert ReviewConfig.model_validate(base).min_severity is Severity.medium
    explicit = ReviewConfig.model_validate({**base, "min_severity": "low"})
    assert explicit.min_severity is Severity.low
