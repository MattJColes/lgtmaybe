"""The `help` command: overview with examples, per-command help, nested paths."""

from __future__ import annotations

import re

from click.testing import CliRunner

from lgtmaybe.cli import main


def test_help_lists_commands_and_examples() -> None:
    result = CliRunner().invoke(main, ["help"])
    assert result.exit_code == 0
    for command in ("review", "comment", "action", "config", "help"):
        assert command in result.output
    assert "Examples:" in result.output
    assert "lgtmaybe review" in result.output
    assert "https://mattjcoles.github.io/lgtmaybe/" in result.output


def test_help_command_matches_dash_dash_help() -> None:
    runner = CliRunner()
    via_help = runner.invoke(main, ["help", "review"])
    via_flag = runner.invoke(main, ["review", "--help"])
    assert via_help.exit_code == 0
    assert via_flag.exit_code == 0
    assert via_help.output == via_flag.output


def test_help_nested_subcommand() -> None:
    runner = CliRunner()
    via_help = runner.invoke(main, ["help", "config", "set"])
    via_flag = runner.invoke(main, ["config", "set", "--help"])
    assert via_help.exit_code == 0
    assert via_help.output == via_flag.output
    assert "KEY VALUE" in via_help.output


def test_help_group_lists_subcommands() -> None:
    result = CliRunner().invoke(main, ["help", "config"])
    assert result.exit_code == 0
    for sub in ("path", "show", "get", "set", "init"):
        assert sub in result.output


def test_help_unknown_command_errors() -> None:
    result = CliRunner().invoke(main, ["help", "bogus"])
    assert result.exit_code != 0
    assert "No such command 'bogus'" in result.output
    assert "lgtmaybe help" in result.output


def test_help_path_through_non_group_errors() -> None:
    result = CliRunner().invoke(main, ["help", "review", "extra"])
    assert result.exit_code != 0
    assert "No such command 'extra'" in result.output


def test_bare_invocation_shows_enriched_help() -> None:
    # Click >= 8.2 treats a group invoked with no args as a usage error
    # (exit 2) while still printing the full help — assert the content only.
    result = CliRunner().invoke(main, [])
    assert "Examples:" in result.output
    for command in ("review", "comment", "action", "config", "help"):
        assert command in result.output


def test_comment_options_all_have_help_text() -> None:
    result = CliRunner().invoke(main, ["comment", "--help"])
    assert result.exit_code == 0
    for line in result.output.splitlines():
        match = re.match(r"^\s+(--[a-z-]+)(?: \S+)?\s*(.*)$", line)
        if match and match.group(1) != "--help":
            assert match.group(2), f"option {match.group(1)} has no help text"
    assert "config file" in result.output
