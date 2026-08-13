"""Click's native overview, command help, and nested-command help."""

from __future__ import annotations

import click
from click.testing import CliRunner

from lgtmaybe.cli import main
from lgtmaybe.core.models import Provider, ReviewPreset, Severity


def test_help_lists_commands_and_examples() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in ("review", "diagram", "comment", "action", "config"):
        assert command in result.output
    assert "  help " not in result.output
    assert "Examples:" in result.output
    assert "lgtmaybe review" in result.output
    assert "https://lgtmaybe.coles.codes/" in result.output


def test_help_examples_cover_every_local_command() -> None:
    """The worked examples are the suggested CLI workflow, so a local command
    missing from them reads as one that doesn't exist — which is exactly how
    `lgtmaybe diagram` got reported as unshipped."""
    result = CliRunner().invoke(main, ["--help"])
    examples = result.output.split("Examples:", 1)[1]
    for command in ("review", "diagram", "config"):
        assert f"lgtmaybe {command}" in examples


def test_help_alias_is_not_a_command() -> None:
    result = CliRunner().invoke(main, ["help"])
    assert result.exit_code == 2
    assert "No such command 'help'" in result.output


def test_help_nested_subcommand() -> None:
    result = CliRunner().invoke(main, ["config", "set", "--help"])
    assert result.exit_code == 0
    assert "KEY VALUE" in result.output


def test_help_group_lists_subcommands() -> None:
    result = CliRunner().invoke(main, ["config", "--help"])
    assert result.exit_code == 0
    for sub in ("path", "show", "get", "set", "init"):
        assert sub in result.output


def test_bare_invocation_shows_enriched_help() -> None:
    # Click >= 8.2 treats a group invoked with no args as a usage error
    # (exit 2) while still printing the full help — assert the content only.
    result = CliRunner().invoke(main, [])
    assert "Examples:" in result.output
    for command in ("review", "comment", "action", "config"):
        assert command in result.output


def test_comment_options_all_have_help_text() -> None:
    result = CliRunner().invoke(main, ["comment", "--help"])
    assert result.exit_code == 0
    for flag, option in _options("comment").items():
        assert option.help, f"option {flag} has no help text"
    assert "config file" in result.output


def _options(command_name: str) -> dict[str, click.Option]:
    """The named command's options, keyed by their primary flag."""
    command = main.get_command(click.Context(main), command_name)
    assert command is not None
    return {p.opts[0]: p for p in command.params if isinstance(p, click.Option)}


def test_shared_options_have_identical_help_across_commands() -> None:
    """`review`, `diagram` and `comment` declare their common flags once, so the
    same flag can never document itself differently depending on the command —
    which is exactly how `--provider` and `--api-base` drifted."""
    review, diagram, comment = (_options(name) for name in ("review", "diagram", "comment"))
    shared = set(review) & set(diagram) & set(comment)
    assert {"--provider", "--model", "--fallback-model", "--api-key", "--api-base"} <= shared
    for flag in shared:
        assert review[flag].help == diagram[flag].help == comment[flag].help, flag
    # --config is spelled the same way but is not part of the model group above.
    assert review["--config"].help == diagram["--config"].help == comment["--config"].help


def test_local_diff_options_have_identical_help_across_commands() -> None:
    """The flags `review` and `diagram` share are declared once too."""
    review, diagram = (_options(name) for name in ("review", "diagram"))
    for flag in ("--base", "--working", "--uncommitted", "--timeout", "--num-ctx"):
        assert review[flag].help == diagram[flag].help, flag


def test_choice_options_are_derived_from_the_enums() -> None:
    """The severity/preset/provider choices come from the enums themselves, so a
    new member can't be missed in one of the three severity lists."""
    review = _options("review")
    severities = [s.value for s in Severity]
    for flag in ("--min-severity", "--fail-on", "--unanchored-min-severity"):
        choice = review[flag].type
        assert isinstance(choice, click.Choice)
        assert [str(c) for c in choice.choices] == severities, flag
    preset = review["--preset"].type
    assert isinstance(preset, click.Choice)
    assert [str(c) for c in preset.choices] == [p.value for p in ReviewPreset]
    provider = review["--provider"].type
    assert isinstance(provider, click.Choice)
    assert list(provider.choices) == [p.value for p in Provider]


def test_provider_choice_accepts_the_hyphenated_wire_value() -> None:
    """`openai-compatible` is the documented spelling — click.Choice over the
    Enum itself would only accept the member NAME (`openai_compatible`)."""
    result = CliRunner().invoke(main, ["comment", "--provider", "openai-compatible", "--help"])
    assert result.exit_code == 0, result.output


def test_unknown_provider_fails_with_a_usage_error() -> None:
    result = CliRunner().invoke(main, ["review", "--provider", "bogus"])
    assert result.exit_code == 2
    assert "bogus" in result.output
    assert "openai-compatible" in result.output
