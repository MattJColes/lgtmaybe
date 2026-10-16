"""Append a Contributors section to a GitHub release's notes.

release-please writes release notes from conventional commits, which carry no
author. GitHub's generate-notes API does know who opened each pull request in
the release, so the release workflow feeds its output here along with the
release's current body, and publishes what this prints.

    python scripts/release_contributors.py GENERATED_NOTES CURRENT_BODY > notes.md

Bots are left out, first-time contributors are welcomed by name, and the
section is fenced by a marker so a re-run replaces it rather than stacking a
second copy. CHANGELOG.md is never touched: the docs' What's New list parses it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MARKER = "<!-- lgtmaybe-contributors -->"

# GitHub's generated lines: "* <title> by @<login> in <url>" and, under
# "## New Contributors", "* @<login> made their first contribution in <url>".
# Anchored to the line end: the title before it is author-written and may
# itself contain "by @someone in https://", which must not credit (or ping)
# anyone. GitHub always appends the real author last.
_AUTHOR = re.compile(r" by @(?P<login>[A-Za-z0-9-]+(?:\[bot\])?) in https://\S+\s*$")
_FIRST = re.compile(r"^\* @(?P<login>[A-Za-z0-9-]+(?:\[bot\])?) made their first contribution")
# Automation accounts that open PRs without a `[bot]` suffix in the login.
_BOT_LOGINS = {"dependabot", "renovate", "github-actions", "release-please"}


def _is_bot(login: str) -> bool:
    return login.endswith("[bot]") or login.lower() in _BOT_LOGINS


def contributors(generated: str) -> tuple[list[str], list[str]]:
    """Human PR authors in first-seen order, and the first-timers among them."""
    people: list[str] = []
    first_timers: list[str] = []
    for line in generated.splitlines():
        if (first := _FIRST.match(line)) and not _is_bot(first["login"]):
            if first["login"] not in first_timers:
                first_timers.append(first["login"])
            continue
        if (match := _AUTHOR.search(line)) and not _is_bot(match["login"]):
            if match["login"] not in people:
                people.append(match["login"])
    return people, first_timers


def _mentions(logins: list[str]) -> str:
    handles = [f"@{login}" for login in logins]
    if len(handles) == 1:
        return handles[0]
    return ", ".join(handles[:-1]) + f" and {handles[-1]}"


def _section(people: list[str], first_timers: list[str]) -> str:
    lines = [MARKER, "### Contributors", "", f"Thanks to {_mentions(people)} for this release."]
    if first_timers:
        lines += ["", f"Welcome to {_mentions(first_timers)}, with a first contribution. 🎉"]
    lines.append(MARKER)
    return "\n".join(lines)


def with_contributors(body: str, generated: str) -> str:
    """*body* with its Contributors section added, or replaced on a re-run."""
    base = body.split(MARKER, 1)[0].rstrip() if MARKER in body else body.rstrip()
    people, first_timers = contributors(generated)
    if not people:
        return f"{base}\n"
    return f"{base}\n\n{_section(people, first_timers)}\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    generated, current = (Path(arg).read_text(encoding="utf-8") for arg in argv)
    sys.stdout.write(with_contributors(current, generated))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
