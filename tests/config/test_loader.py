"""Tests for the .lgtmaybe.yml config loader.

Precedence: CLI inputs > repo config file > defaults.
"""

from __future__ import annotations

import pytest

from lgtmaybe.config.loader import load_config


def test_empty_file_yields_defaults(tmp_path):
    """An empty YAML file produces a valid ReviewConfig with ollama defaults."""
    cfg_file = tmp_path / ".lgtmaybe.yml"
    cfg_file.write_text("")

    cfg = load_config(config_path=cfg_file)

    assert cfg.provider == "ollama"
    assert cfg.model == "llama3"


def test_missing_file_yields_defaults(tmp_path):
    """A missing config file produces working defaults without error."""
    cfg = load_config(config_path=tmp_path / ".lgtmaybe.yml")

    assert cfg.provider == "ollama"
    assert cfg.model == "llama3"


def test_file_values_are_applied():
    """Values in the config file are reflected in the returned ReviewConfig."""
    import io

    yaml_content = "provider: anthropic\nmodel: claude-3-5-sonnet-20241022\nmin_severity: medium\n"

    cfg = load_config(config_stream=io.StringIO(yaml_content))

    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-3-5-sonnet-20241022"
    assert cfg.min_severity == "medium"


def test_cli_input_overrides_file_value():
    """An explicit CLI input takes precedence over the file's value."""
    import io

    yaml_content = "provider: openai\nmodel: gpt-4o\nmin_severity: low\n"

    cfg = load_config(config_stream=io.StringIO(yaml_content), min_severity="high")

    assert cfg.min_severity == "high"
    # File values still applied for keys not overridden
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-4o"


def test_context_lines_defaults_and_overrides():
    """context_lines defaults to 20, is read from file, and can be overridden (incl. 0)."""
    import io

    assert (
        load_config(config_stream=io.StringIO("provider: openai\nmodel: gpt-4o\n")).context_lines
        == 20
    )

    from_file = load_config(
        config_stream=io.StringIO("provider: openai\nmodel: gpt-4o\ncontext_lines: 5\n")
    )
    assert from_file.context_lines == 5

    overridden = load_config(
        config_stream=io.StringIO("provider: openai\nmodel: gpt-4o\ncontext_lines: 5\n"),
        context_lines=0,
    )
    assert overridden.context_lines == 0


def test_max_input_tokens_and_num_ctx_defaults_and_overrides():
    """max_input_tokens (any provider) and num_ctx (ollama) load from file and CLI."""
    import io

    base = load_config(config_stream=io.StringIO("provider: ollama\nmodel: llama3\n"))
    assert base.max_input_tokens == 100_000
    assert base.num_ctx is None

    overridden = load_config(
        config_stream=io.StringIO("provider: ollama\nmodel: llama3\n"),
        max_input_tokens=250_000,
        num_ctx=32768,
    )
    assert overridden.max_input_tokens == 250_000
    assert overridden.num_ctx == 32768


def test_cli_input_overrides_provider():
    """A CLI --provider overrides the file's provider."""
    import io

    yaml_content = "provider: openai\nmodel: gpt-4o\n"

    cfg = load_config(config_stream=io.StringIO(yaml_content), provider="anthropic")

    assert cfg.provider == "anthropic"


def test_unknown_key_in_yaml_raises():
    """An unknown key in the YAML file is rejected with a clear error (extra=forbid)."""
    import io

    yaml_content = "provider: ollama\nmodel: llama3\nunknown_key: bad\n"

    with pytest.raises(Exception, match="unknown_key|extra"):
        load_config(config_stream=io.StringIO(yaml_content))


def test_user_config_is_used_when_no_project_file(tmp_path):
    """A value in the user-level config is applied when no repo file overrides it."""
    user_cfg = tmp_path / "config.yml"
    user_cfg.write_text("provider: anthropic\nmodel: claude-3-5-sonnet-20241022\n")

    cfg = load_config(user_config_path=user_cfg)

    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-3-5-sonnet-20241022"


def test_project_file_overrides_user_config(tmp_path):
    """The repo .lgtmaybe.yml takes precedence over the user-level config."""
    user_cfg = tmp_path / "config.yml"
    user_cfg.write_text("provider: anthropic\nmodel: user-model\n")
    project_cfg = tmp_path / ".lgtmaybe.yml"
    project_cfg.write_text("model: project-model\n")

    cfg = load_config(config_path=project_cfg, user_config_path=user_cfg)

    assert cfg.provider == "anthropic"  # from user config
    assert cfg.model == "project-model"  # repo file wins


def test_cli_overrides_user_and_project(tmp_path):
    """An explicit CLI input beats both the project file and the user config."""
    user_cfg = tmp_path / "config.yml"
    user_cfg.write_text("provider: anthropic\nmodel: user-model\n")
    project_cfg = tmp_path / ".lgtmaybe.yml"
    project_cfg.write_text("model: project-model\n")

    cfg = load_config(config_path=project_cfg, user_config_path=user_cfg, model="cli-model")

    assert cfg.model == "cli-model"


def test_none_cli_inputs_do_not_override():
    """CLI inputs that are None (not passed) do not clobber file or default values."""
    import io

    yaml_content = "provider: openai\nmodel: gpt-4o\nmin_severity: high\n"

    # Passing None explicitly — simulates a click option that wasn't supplied
    cfg = load_config(config_stream=io.StringIO(yaml_content), provider=None, min_severity=None)

    assert cfg.provider == "openai"
    assert cfg.min_severity == "high"


def test_inline_extra_lenses_load_from_yml(tmp_path):
    """`extra_lenses` defined inline in .lgtmaybe.yml reach the ReviewConfig."""
    cfg_file = tmp_path / ".lgtmaybe.yml"
    cfg_file.write_text(
        "provider: ollama\n"
        "model: qwen3.6:27b\n"
        "extra_lenses:\n"
        "  - id: simplify\n"
        "    title: Simplify or delete\n"
        "    instructions: Flag needless code.\n"
    )

    cfg = load_config(config_path=cfg_file)

    assert [lens.id for lens in cfg.extra_lenses] == ["simplify"]


def test_lens_paths_load_skill_files_from_dir(tmp_path):
    """`lens_paths` pointing at a directory loads every *.yml lens file in it,
    and the directive itself is consumed (never reaches the strict ReviewConfig)."""
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "simplify.yml").write_text(
        "id: simplify\ntitle: Simplify or delete\ninstructions: Flag needless code.\n"
    )
    (skills / "house.yml").write_text("id: house-style\ninstructions: Enforce house style.\n")

    cfg_file = tmp_path / ".lgtmaybe.yml"
    cfg_file.write_text(f"provider: ollama\nmodel: m\nlens_paths:\n  - {skills}\n")

    cfg = load_config(config_path=cfg_file)

    assert sorted(lens.id for lens in cfg.extra_lenses) == ["house-style", "simplify"]


def test_lens_paths_accept_a_list_of_lenses_in_one_file(tmp_path):
    """A single lens file may hold a YAML list of lenses."""
    lens_file = tmp_path / "lenses.yml"
    lens_file.write_text("- id: a\n  instructions: first\n- id: b\n  instructions: second\n")
    cfg_file = tmp_path / ".lgtmaybe.yml"
    cfg_file.write_text(f"provider: ollama\nmodel: m\nlens_paths:\n  - {lens_file}\n")

    cfg = load_config(config_path=cfg_file)

    assert sorted(lens.id for lens in cfg.extra_lenses) == ["a", "b"]


def test_lens_paths_pack_scheme_loads_a_bundled_pack(tmp_path):
    """`lens_paths: [pack:<name>]` resolves to a curated pack shipped in the package,
    so pip-installed users enable it by name (no repo-relative path exists for them)."""
    cfg_file = tmp_path / ".lgtmaybe.yml"
    cfg_file.write_text("provider: ollama\nmodel: m\nlens_paths:\n  - pack:design\n")

    cfg = load_config(config_path=cfg_file)

    # The bundled "design" pack ships several curated lenses; each must be valid.
    assert len(cfg.extra_lenses) >= 5
    assert len({lens.id for lens in cfg.extra_lenses}) == len(cfg.extra_lenses)


def test_lens_paths_can_combine_several_bundled_packs(tmp_path):
    """Several `pack:` entries compose into one lens set with unique ids."""
    cfg_file = tmp_path / ".lgtmaybe.yml"
    cfg_file.write_text(
        "provider: ollama\nmodel: m\nlens_paths:\n  - pack:design\n  - pack:robustness\n"
    )

    cfg = load_config(config_path=cfg_file)

    ids = [lens.id for lens in cfg.extra_lenses]
    assert "wrong-abstraction" in ids  # from design
    assert "bounded" in ids  # from robustness
    assert len(ids) == len(set(ids))


def test_lens_paths_unknown_pack_fails_clearly(tmp_path):
    """An unknown bundled pack name fails loudly, naming the packs that do exist."""
    cfg_file = tmp_path / ".lgtmaybe.yml"
    cfg_file.write_text("provider: ollama\nmodel: m\nlens_paths:\n  - pack:nope\n")

    with pytest.raises(ValueError, match="nope|pack"):
        load_config(config_path=cfg_file)
