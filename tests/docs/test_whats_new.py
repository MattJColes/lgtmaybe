"""The docs homepage's What's New list and the changelog page are built from
CHANGELOG.md at docs-build time, so they can never drift from a release.

A committed, regenerated block would go stale on every release-please PR (which
rewrites CHANGELOG.md but not the docs), so there is no generated file to keep
fresh here — only the hook's parsing and rendering to pin down.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(_ROOT / "docs"))

from whats_new import (  # noqa: E402
    LATEST,
    MARKER,
    changelog_page,
    on_page_markdown,
    parse_changelog,
    whats_new,
)

_SAMPLE = """\
# Changelog

## [2.1.0](https://github.com/o/r/compare/r-v2.0.0...r-v2.1.0) (2026-10-13)


### ⚠ BREAKING CHANGES

* **engine:** drop the legacy cache ([#9](https://github.com/o/r/issues/9))

### Features

* **cli:** add --format sarif ([#7](https://github.com/o/r/issues/7)) ([abc1234](https://github.com/o/r/commit/abc1234))
* calibrate reviews with a project profile ([#6](https://github.com/o/r/issues/6)) ([def5678](https://github.com/o/r/commit/def5678))
* serve reviews over MCP ([#5](https://github.com/o/r/issues/5)) ([0a0a0a0](https://github.com/o/r/commit/0a0a0a0))


### Bug Fixes

* **gitea:** don't stop paging at a short page ([#4](https://github.com/o/r/issues/4)) ([1111111](https://github.com/o/r/commit/1111111))

## [2.0.1](https://github.com/o/r/compare/r-v2.0.0...r-v2.0.1) (2026-10-12)


### Bug Fixes

* bind summaries to native heads ([#3](https://github.com/o/r/issues/3)) ([2222222](https://github.com/o/r/commit/2222222))

## Changelog
"""


def test_parse_reads_each_release_and_ignores_stray_headings() -> None:
    releases = parse_changelog(_SAMPLE)

    assert [r.version for r in releases] == ["2.1.0", "2.0.1"]
    first = releases[0]
    assert first.date == "2026-10-13"
    assert first.compare_url == "https://github.com/o/r/compare/r-v2.0.0...r-v2.1.0"
    assert list(first.sections) == ["⚠ BREAKING CHANGES", "Features", "Bug Fixes"]
    assert len(first.sections["Features"]) == 3


def test_headlines_drop_the_pr_and_commit_links() -> None:
    first = parse_changelog(_SAMPLE)[0]

    assert first.headlines(2) == [
        "cli: add --format sarif",
        "calibrate reviews with a project profile",
    ]


def test_a_fix_only_release_headlines_its_fixes() -> None:
    assert parse_changelog(_SAMPLE)[1].headlines(2) == ["bind summaries to native heads"]


def test_whats_new_links_each_release_to_its_changelog_anchor() -> None:
    text = whats_new(parse_changelog(_SAMPLE), limit=5)

    assert "[**2.1.0**](changelog.md#v2-1-0)" in text
    assert "[**2.0.1**](changelog.md#v2-0-1)" in text
    assert "2026-10-13" in text
    assert "⚠ breaking" in text  # a breaking release says so up front
    assert "and 2 more changes" in text  # 3 features + 1 fix, 2 headlined
    assert "[Full changelog](changelog.md)" in text


def test_whats_new_shows_only_the_latest_releases() -> None:
    text = whats_new(parse_changelog(_SAMPLE), limit=1)

    assert "2.1.0" in text
    assert "2.0.1" not in text


def test_changelog_page_gives_every_release_a_stable_anchor() -> None:
    page = changelog_page(parse_changelog(_SAMPLE))

    assert page.startswith("# Changelog")
    assert "## 2.1.0 {#v2-1-0}" in page
    assert "## 2.0.1 {#v2-0-1}" in page
    # Full detail survives on the changelog page, links included.
    assert "([#7](https://github.com/o/r/issues/7))" in page
    assert "[Compare with the previous release]" in page


class _Page:
    def __init__(self, src_uri: str) -> None:
        self.file = type("F", (), {"src_uri": src_uri})()


def test_the_hook_fills_only_the_homepage_marker() -> None:
    markdown = f"intro\n\n{MARKER}\n\noutro"

    filled = on_page_markdown(markdown, page=_Page("index.md"))
    assert MARKER not in filled
    assert "changelog.md#v" in filled

    untouched = on_page_markdown(markdown, page=_Page("how-to/x.md"))
    assert untouched == markdown


def test_the_real_changelog_yields_the_latest_five() -> None:
    releases = parse_changelog((_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert len(releases) >= LATEST
    assert whats_new(releases).count("](changelog.md#v") == LATEST


def test_the_homepage_carries_the_marker_and_the_build_runs_the_hook() -> None:
    homepage = (_ROOT / "docs/index.md").read_text(encoding="utf-8")
    mkdocs = (_ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    workflow = (_ROOT / ".github/workflows/docs.yml").read_text(encoding="utf-8")

    assert "## What's New" in homepage
    assert MARKER in homepage
    assert "docs/whats_new.py" in mkdocs
    assert "Changelog: changelog.md" in mkdocs
    # A release only touches CHANGELOG.md, so it must trigger a docs rebuild.
    assert '"CHANGELOG.md"' in workflow


def test_on_files_registers_the_generated_changelog_page(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The hook's only contact with the mkdocs API is File.generated; pin the call
    here, since the dev environment does not install mkdocs. The docs workflow's
    strict build on every PR then checks it against the real API."""
    import types

    import whats_new

    calls: list[tuple[object, str, str]] = []

    class _File:
        @staticmethod
        def generated(config: object, src_uri: str, *, content: str) -> str:
            calls.append((config, src_uri, content))
            return src_uri

    fake = types.ModuleType("mkdocs.structure.files")
    fake.File = _File  # type: ignore[attr-defined]
    for name in ("mkdocs", "mkdocs.structure"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "mkdocs.structure.files", fake)

    files: list[str] = []
    config = object()
    assert whats_new.on_files(files, config) is files

    ((got_config, src_uri, content),) = calls
    assert got_config is config
    assert src_uri == "changelog.md"
    assert content.startswith("# Changelog")
    assert files == ["changelog.md"]


def test_llms_generator_shares_the_hooks_marker() -> None:
    """One marker, owned by the hook: a second copy would drift silently."""
    import generate_llms_txt

    assert generate_llms_txt.MARKER is MARKER
    assert not hasattr(generate_llms_txt, "_WHATS_NEW_MARKER")


def test_every_pr_builds_the_docs_but_only_main_deploys() -> None:
    """The hook can break the build, so a PR must run it — without publishing."""
    workflow = (_ROOT / ".github/workflows/docs.yml").read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    # The upload step and the deploy job are each gated, not merely the file.
    import yaml

    jobs = yaml.safe_load(workflow)["jobs"]
    gate = "github.event_name != 'pull_request'"
    (upload,) = [
        step
        for step in jobs["build"]["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-pages-artifact")
    ]
    assert upload.get("if") == gate
    assert jobs["deploy"].get("if") == gate
