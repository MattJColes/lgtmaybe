"""Structural guards for ``.pre-commit-hooks.yaml`` and its how-to.

The hook entries are command lines the pre-commit framework runs verbatim, so a
renamed command or flag would only surface on a user's commit. These tests bind
each entry (and the documented example's ``args``) to the real Click tree.
"""

from __future__ import annotations

import re
import shlex
import tomllib
from pathlib import Path

import click
import yaml

from lgtmaybe.cli import FAIL_ON_EXIT_CODE, main

_ROOT = Path(__file__).parent.parent
_HOOKS = _ROOT / ".pre-commit-hooks.yaml"
_PYPROJECT = _ROOT / "pyproject.toml"
_HOW_TO = _ROOT / "docs" / "how-to" / "review-before-you-commit.md"
_MKDOCS = _ROOT / "mkdocs.yml"
_README = _ROOT / "README.md"

# Stage names pre-commit >= 3.2 accepts (the pre-3.2 aliases `commit`/`push`
# are deprecated, which is why the hooks pin minimum_pre_commit_version).
_STAGES = {
    "commit-msg",
    "manual",
    "post-checkout",
    "post-commit",
    "post-merge",
    "post-rewrite",
    "pre-commit",
    "pre-merge-commit",
    "pre-push",
    "pre-rebase",
    "prepare-commit-msg",
}


def _hooks() -> dict[str, dict]:
    hooks = yaml.safe_load(_HOOKS.read_text(encoding="utf-8"))
    assert isinstance(hooks, list)
    return {hook["id"]: hook for hook in hooks}


def _console_script() -> str:
    scripts = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]["scripts"]
    (name,) = [name for name, target in scripts.items() if target == "lgtmaybe.cli:main"]
    return name


def _parse(argv: list[str]) -> click.Context:
    """Resolve *argv* (after the program name) on the real command tree.

    ``make_context`` parses without invoking, so an unknown command, flag or
    choice value raises here instead of on a user's commit.
    """
    command_name, *args = argv
    command = main.get_command(click.Context(main), command_name)
    assert command is not None, f"no `{command_name}` command"
    return command.make_context(command_name, args)


def test_defines_the_two_hooks() -> None:
    assert set(_hooks()) == {"lgtmaybe", "lgtmaybe-pre-push"}


def test_commit_hook_reviews_uncommitted_changes() -> None:
    hook = _hooks()["lgtmaybe"]

    assert hook["entry"] == "lgtmaybe review --uncommitted"
    assert hook["stages"] == ["pre-commit"]


def test_push_hook_reviews_the_branch() -> None:
    hook = _hooks()["lgtmaybe-pre-push"]

    assert hook["entry"] == "lgtmaybe review"
    assert hook["stages"] == ["pre-push"]


def test_hooks_compute_their_own_diff() -> None:
    for hook_id, hook in _hooks().items():
        # lgtmaybe diffs git itself: filenames on argv would be parsed as
        # unexpected arguments, and the hook must run even with no matching file.
        assert hook["pass_filenames"] is False, hook_id
        assert hook["always_run"] is True, hook_id
        assert hook["language"] == "python", hook_id
        # pre-commit hides a passing hook's output; an advisory review that
        # exits 0 would otherwise print nothing the developer ever sees.
        assert hook["verbose"] is True, hook_id
        assert hook["description"], hook_id
        assert set(hook["stages"]) <= _STAGES, hook_id
        # The pre-commit / pre-push stage names arrived in pre-commit 3.2.0.
        assert hook["minimum_pre_commit_version"] == "3.2.0", hook_id


def test_entries_run_the_installed_console_script() -> None:
    script = _console_script()
    for hook_id, hook in _hooks().items():
        assert shlex.split(hook["entry"])[0] == script, hook_id


def test_entries_resolve_on_the_click_tree() -> None:
    for hook_id, hook in _hooks().items():
        ctx = _parse(shlex.split(hook["entry"])[1:])
        assert ctx.command.name == "review", hook_id


def test_commit_hook_entry_sets_uncommitted() -> None:
    ctx = _parse(shlex.split(_hooks()["lgtmaybe"]["entry"])[1:])

    assert ctx.params["uncommitted"] is True
    assert ctx.params["working"] is False


def _documented_repos() -> list[dict]:
    """Every repo entry of every ``.pre-commit-config.yaml`` the how-to shows."""
    text = _HOW_TO.read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n(.*?)```", text, flags=re.DOTALL)
    repos = [
        repo for block in blocks if "repos:" in block for repo in yaml.safe_load(block)["repos"]
    ]
    assert repos, "the how-to shows no .pre-commit-config.yaml"
    return repos


def test_documented_configs_pin_a_release_tag_and_known_hooks() -> None:
    for repo in _documented_repos():
        assert repo["repo"] == "https://github.com/MattJColes/lgtmaybe"
        assert re.fullmatch(r"v\d+\.\d+\.\d+", repo["rev"]), repo["rev"]
        assert {hook["id"] for hook in repo["hooks"]} <= set(_hooks())


def test_documented_args_parse_on_the_hook_entries() -> None:
    hooks = _hooks()
    for repo in _documented_repos():
        for documented in repo["hooks"]:
            entry = shlex.split(hooks[documented["id"]]["entry"])[1:]
            ctx = _parse([*entry, *documented.get("args", [])])
            assert ctx.command.name == "review"


def test_documented_example_gates_on_fail_on() -> None:
    hooks = [hook for repo in _documented_repos() for hook in repo["hooks"]]

    assert any("--fail-on" in hook.get("args", []) for hook in hooks)
    assert f"exit code {FAIL_ON_EXIT_CODE}" in _HOW_TO.read_text(encoding="utf-8")


def test_how_to_is_in_the_nav_and_the_readme() -> None:
    assert "how-to/review-before-you-commit.md" in _MKDOCS.read_text(encoding="utf-8")
    assert "how-to/review-before-you-commit" in _README.read_text(encoding="utf-8")
