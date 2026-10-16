"""mkdocs hook: the homepage's What's New list and a Changelog page, from CHANGELOG.md.

Both are built when the docs build, from the CHANGELOG.md release-please
writes, so neither can go stale: a release commit touches CHANGELOG.md and
the docs workflow rebuilds on it. Nothing is generated into the repository,
because release-please's own PR rewrites CHANGELOG.md and nothing else, so a
committed copy would always be one release behind.

Homepage entries link to anchors on the generated changelog page rather than
to GitHub release pages: every version has a CHANGELOG.md entry, while not
every version has a GitHub release.

Kept free of mkdocs imports at module level so the parsing and rendering are
testable from the dev environment, which does not install the docs group.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MARKER = "<!-- whats-new -->"
LATEST = 5

_CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
_RELEASE = re.compile(
    r"^## \[?(?P<version>\d+\.\d+\.\d+)\]?"
    r"(?:\((?P<url>[^)]+)\))?"
    r" \((?P<date>\d{4}-\d{2}-\d{2})\)\s*$"
)
_SECTION = re.compile(r"^### (?P<name>.+?)\s*$")
_BULLET = re.compile(r"^\* (?P<text>.+)$")
# release-please appends `([#123](…/issues/123))` and `([abc1234](…/commit/…))`.
_TRAILING_LINK = re.compile(r"\s*\(\[[^\]]+\]\([^)]+\)\)")
# The sections a reader means by "what changed", in headline order.
_HEADLINE_SECTIONS = ("Features", "Performance Improvements", "Bug Fixes")
_BREAKING = "⚠ BREAKING CHANGES"


@dataclass
class Release:
    version: str
    date: str
    compare_url: str | None
    sections: dict[str, list[str]] = field(default_factory=dict)

    @property
    def anchor(self) -> str:
        return "v" + self.version.replace(".", "-")

    def _changes(self) -> list[str]:
        return [item for name in _HEADLINE_SECTIONS for item in self.sections.get(name, [])]

    def headlines(self, count: int) -> list[str]:
        """The first *count* changes as plain text: no links, no bold scope."""
        return [_plain(item) for item in self._changes()[:count]]

    def more(self, shown: int) -> int:
        return max(len(self._changes()) - shown, 0)


def _plain(item: str) -> str:
    text = _TRAILING_LINK.sub("", item)
    return text.replace("**", "").strip()


def parse_changelog(text: str) -> list[Release]:
    """Every release heading in *text*, newest first, with its bulleted sections."""
    releases: list[Release] = []
    section: list[str] | None = None
    for line in text.splitlines():
        if heading := _RELEASE.match(line):
            releases.append(Release(heading["version"], heading["date"], heading["url"]))
            section = None
        elif line.startswith("## "):
            section = None  # a stray heading ends the current release's sections
        elif releases and (name := _SECTION.match(line)):
            section = releases[-1].sections.setdefault(name["name"], [])
        elif section is not None and (bullet := _BULLET.match(line)):
            section.append(bullet["text"])
    return releases


def whats_new(releases: list[Release], limit: int = LATEST) -> str:
    """The homepage list: the latest *limit* releases, linked to the changelog page."""
    lines = []
    for release in releases[:limit]:
        parts = [f"[**{release.version}**](changelog.md#{release.anchor})", release.date]
        if release.sections.get(_BREAKING):
            parts.append("⚠ breaking")
        headlines = release.headlines(2)
        summary = "; ".join(headlines) if headlines else _maintenance(release)
        more = release.more(len(headlines))
        if more:
            summary += f"; and {more} more change{'s' if more != 1 else ''}"
        parts.append(summary)
        lines.append("- " + " · ".join(parts))
    lines += ["", "[Full changelog](changelog.md)"]
    return "\n".join(lines)


def _maintenance(release: Release) -> str:
    names = ", ".join(name.lower() for name in release.sections) or "housekeeping"
    return f"maintenance ({names})"


def changelog_page(releases: list[Release]) -> str:
    """The full Changelog page, one stable `#vX-Y-Z` anchor per release."""
    out = [
        "# Changelog",
        "",
        "Every lgtmaybe release, newest first. Built from `CHANGELOG.md`, which "
        "release-please writes from conventional commits.",
    ]
    for release in releases:
        out += ["", f"## {release.version} {{#{release.anchor}}}", ""]
        meta = f"Released {release.date}"
        if release.compare_url:
            meta += f" · [Compare with the previous release]({release.compare_url})"
        out.append(meta)
        for name, items in release.sections.items():
            out += ["", f"### {name}", ""]
            out += [f"- {item}" for item in items]
    return "\n".join(out) + "\n"


def _releases() -> list[Release]:
    return parse_changelog(_CHANGELOG.read_text(encoding="utf-8"))


# --- mkdocs hook events ------------------------------------------------------


def on_files(files: Any, config: Any, **_: Any) -> Any:
    from mkdocs.structure.files import File

    page = File.generated(config, "changelog.md", content=changelog_page(_releases()))
    files.append(page)
    return files


def on_page_markdown(markdown: str, page: Any, **_: Any) -> str:
    if page.file.src_uri != "index.md" or MARKER not in markdown:
        return markdown
    return markdown.replace(MARKER, whats_new(_releases()))
