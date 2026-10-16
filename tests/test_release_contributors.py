"""Release notes thank the people who contributed to each release.

release-please writes the changelog from conventional commits, which carry no
author, so the release workflow asks GitHub's generate-notes API who authored
the release's pull requests and appends a Contributors section to the GitHub
release. CHANGELOG.md is left alone: the docs' What's New list parses it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

from release_contributors import MARKER, contributors, with_contributors  # noqa: E402

_GENERATED = """\
## What's Changed
* feat(engine): add architecture review lens by @MattJColes in https://github.com/o/r/pull/734
* fix(docs): typo by @first-timer in https://github.com/o/r/pull/735
* chore(deps): bump x by @dependabot[bot] in https://github.com/o/r/pull/736
* chore(main): release 2.13.0 by @github-actions[bot] in https://github.com/o/r/pull/737
* feat: another by @MattJColes in https://github.com/o/r/pull/738

## New Contributors
* @first-timer made their first contribution in https://github.com/o/r/pull/735

**Full Changelog**: https://github.com/o/r/compare/r-v2.12.0...r-v2.13.0
"""


def test_contributors_are_the_human_pr_authors_in_first_seen_order() -> None:
    people, first_timers = contributors(_GENERATED)

    assert people == ["MattJColes", "first-timer"]
    assert first_timers == ["first-timer"]


def test_the_section_thanks_everyone_and_welcomes_first_timers() -> None:
    notes = with_contributors("## Features\n\n* thing", _GENERATED)

    assert notes.startswith("## Features\n\n* thing")
    assert MARKER in notes
    assert "### Contributors" in notes
    assert "@MattJColes" in notes and "@first-timer" in notes
    assert "dependabot" not in notes and "github-actions" not in notes
    assert "first contribution" in notes


def test_rerunning_replaces_the_section_instead_of_stacking_it() -> None:
    once = with_contributors("body", _GENERATED)
    twice = with_contributors(once, _GENERATED)

    assert twice == once
    assert twice.count("### Contributors") == 1


def test_a_release_with_only_bot_authors_is_left_unchanged() -> None:
    bots_only = "* chore(deps): bump x by @dependabot[bot] in https://github.com/o/r/pull/1\n"

    assert with_contributors("body", bots_only) == "body"


def test_the_release_workflow_runs_it_after_the_release_is_cut() -> None:
    workflow = (_ROOT / ".github/workflows/release-please.yml").read_text(encoding="utf-8")

    assert "contributors:" in workflow
    assert "scripts/release_contributors.py" in workflow
    assert "releases/generate-notes" in workflow
    # Expressions reach the shell through env, never interpolated into `run:`.
    run_blocks = workflow.split("contributors:", 1)[1].split("\n  pypi:", 1)[0]
    assert "${{ needs.release-please.outputs.tag_name }}" not in run_blocks.split("run:", 1)[1]
