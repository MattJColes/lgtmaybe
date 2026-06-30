"""Generate docs/llms.txt and docs/llms-full.txt for LLM crawlers.

Run with:
    uv run python docs/generate_llms_txt.py

`llms.txt` follows the https://llmstxt.org/ convention: an H1, a blockquote
summary, then one `## ` section per Diátaxis group with curated links. Each
link's description is the page's own `description` front-matter, so the index
stays in sync with the per-page meta descriptions. `llms-full.txt` is the same
sections with the full page text inlined, for whole-corpus ingestion.

The output is deterministic (nav order is stable) so CI can detect drift with a
byte-for-byte comparison — see tests/docs/test_llms_txt_fresh.py. The site is
served from a subpath (GitHub project Pages), so every link is an absolute URL
under SITE_URL rather than a root-relative path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DOCS = Path(__file__).parent
MKDOCS_PATH = DOCS.parent / "mkdocs.yml"
LLMS_PATH = DOCS / "llms.txt"
LLMS_FULL_PATH = DOCS / "llms-full.txt"

SITE_URL = "https://mattjcoles.github.io/lgtmaybe/"

SUMMARY = (
    "Provider-agnostic AI pull-request reviewer. It posts inline review comments "
    "and a summary onto a GitHub pull request, or prints findings locally from "
    "your git diff. Seven hosted providers, local ollama, and any "
    "OpenAI-compatible endpoint — one flag, and keyless OIDC/WIF auth for cloud "
    "providers (no static keys in secrets)."
)


def _load_nav() -> list[Any]:
    """Return the nav list from mkdocs.yml."""
    config = yaml.safe_load(MKDOCS_PATH.read_text(encoding="utf-8"))
    nav = config["nav"]
    assert isinstance(nav, list)
    return nav


def _front_matter(md_rel: str) -> dict[str, Any]:
    """Parse the YAML front-matter of a docs page, or {} if it has none."""
    text = (DOCS / md_rel).read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    _, fm, _ = text.split("---", 2)
    return yaml.safe_load(fm) or {}


def _body(md_rel: str) -> str:
    """Return a docs page's content with any front-matter stripped."""
    text = (DOCS / md_rel).read_text(encoding="utf-8")
    if text.startswith("---"):
        return text.split("---", 2)[2].lstrip("\n")
    return text


def _page_url(md_rel: str) -> str:
    """Map a docs-relative .md path to its absolute built URL (directory URLs)."""
    stem = md_rel[:-3]  # drop ".md"
    if stem == "index":
        return SITE_URL
    return f"{SITE_URL}{stem}/"


def _sections() -> tuple[str | None, list[tuple[str, list[tuple[str, str]]]]]:
    """Walk the nav into (home_md, [(section_name, [(title, md_rel)])]).

    The Home entry is returned separately: it seeds the summary and the
    full-text dump but is not repeated as a link in the section list.
    """
    home_md: str | None = None
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    for entry in _load_nav():
        ((label, value),) = entry.items()
        if isinstance(value, str):
            if value == "index.md":
                home_md = value
            else:
                sections.append((label, [(label, value)]))
        else:
            pages = [next(iter(sub.items())) for sub in value]
            sections.append((label, pages))
    return home_md, sections


def generate_index() -> str:
    """Return the content of llms.txt."""
    _, sections = _sections()
    lines: list[str] = ["# lgtmaybe", "", f"> {SUMMARY}", ""]
    for section_name, pages in sections:
        lines.append(f"## {section_name}")
        lines.append("")
        for title, md_rel in pages:
            desc = str(_front_matter(md_rel).get("description", "")).strip()
            url = _page_url(md_rel)
            lines.append(f"- [{title}]({url}): {desc}" if desc else f"- [{title}]({url})")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def generate_full() -> str:
    """Return the content of llms-full.txt (sections with full page text)."""
    home_md, sections = _sections()
    ordered: list[tuple[str, str]] = []
    if home_md is not None:
        ordered.append(("Home", home_md))
    for _, pages in sections:
        ordered.extend(pages)

    parts: list[str] = [
        "# lgtmaybe — full documentation",
        "",
        f"> {SUMMARY}",
        "",
        f"Source: {SITE_URL} — generated from the docs/ tree.",
        "",
    ]
    for _title, md_rel in ordered:
        parts.append("---")
        parts.append("")
        parts.append(f"<!-- Source: {_page_url(md_rel)} -->")
        parts.append("")
        parts.append(_body(md_rel).strip())
        parts.append("")
    return "\n".join(parts).rstrip("\n") + "\n"


def main() -> None:
    LLMS_PATH.write_text(generate_index(), encoding="utf-8")
    LLMS_FULL_PATH.write_text(generate_full(), encoding="utf-8")
    print(f"Written: {LLMS_PATH}")
    print(f"Written: {LLMS_FULL_PATH}")


if __name__ == "__main__":
    main()
