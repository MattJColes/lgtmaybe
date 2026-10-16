"""The docs stay readable for an engineer new to lgtmaybe.

Rules live in docs/how-to/write-docs.md; this gate checks the parts a machine
can: sentence length, no em dashes, and project jargon linked to the glossary.
Pages that do not pass yet are listed in ``NOT_YET_PASSING`` and skipped. The
list may only shrink: a listed page that passes fails the ratchet test, so it
comes off the list, and is gated, as soon as it is fixed. Passing the gate is
the floor, not the rewrite: every page still gets a plain-language pass.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from doc_readability import (  # noqa: E402
    KEY_TERMS,
    MAX_AVERAGE_WORDS,
    MAX_SENTENCE_WORDS,
    check_page,
    main,
    prose_units,
    sentences,
)

# Generated from the models; its prose comes from ReviewConfig field docs.
_GENERATED = {"docs/reference/config.md"}

NOT_YET_PASSING: frozenset[str] = frozenset(
    {
        "docs/how-to/add-a-custom-lens.md",
        "docs/how-to/calibrate-reviews-to-your-project.md",
        "docs/how-to/configure-lgtmaybe-yml.md",
        "docs/how-to/fix-findings-with-an-ai-agent.md",
        "docs/how-to/generate-a-change-diagram.md",
        "docs/how-to/reduce-review-cost.md",
        "docs/how-to/releasing.md",
        "docs/how-to/review-on-azure-devops.md",
        "docs/how-to/review-on-gitea.md",
        "docs/how-to/review-on-gitlab.md",
        "docs/how-to/review-with-anthropic.md",
        "docs/how-to/review-with-bedrock-oidc.md",
        "docs/how-to/review-with-openai.md",
        "docs/how-to/review-with-openrouter.md",
        "docs/how-to/run-locally-with-ollama.md",
        "docs/how-to/scope-review-instructions-to-a-directory.md",
        "docs/how-to/use-a-custom-openai-compatible-endpoint.md",
        "docs/how-to/use-as-github-action.md",
        "docs/how-to/use-from-a-coding-agent-over-mcp.md",
        "docs/reference/profile.md",
    }
)


def _pages() -> list[str]:
    docs = sorted(p.relative_to(_ROOT).as_posix() for p in (_ROOT / "docs").rglob("*.md"))
    return ["README.md", *(p for p in docs if p not in _GENERATED)]


_PAGES = _pages()


# --- the checker itself ----------------------------------------------------------


def test_code_tables_and_front_matter_are_not_prose() -> None:
    page = """---
description: a very long front matter line that is not prose at all and never counts
---

# A Heading That Is Not a Sentence

Run it.

```bash
lgtmaybe review --provider ollama --model qwen3.6:27b --base origin/main --profile
```

| Column | A table cell with a great many words that would otherwise count |
|---|---|

<div class="grid cards" markdown>
</div>
"""
    assert prose_units(page) == ["Run it."]


def test_inline_code_and_links_count_as_one_word() -> None:
    (unit,) = prose_units("Set `max_input_tokens: 100000` in [the config](../how-to/x.md#y).")
    assert sentences(unit) == ["Set CODE in the config."]


def test_abbreviations_and_decimals_do_not_end_a_sentence() -> None:
    text = "Use a model, e.g. qwen3.6 or GPT-5.1, i.e. a big one. It costs 0.5 cents."
    assert len(sentences(text)) == 2


def test_a_sentence_may_start_with_a_lowercase_name() -> None:
    """lgtmaybe and ollama are spelt lowercase, even at the start of a sentence."""
    assert len(sentences("It is fast. lgtmaybe reads the diff. ollama runs locally.")) == 3


def test_each_list_item_is_its_own_unit() -> None:
    units = prose_units("- first item\n- second item\n  continues here\n")
    assert units == ["first item", "second item continues here"]


def test_admonition_titles_are_not_prose_but_bodies_are() -> None:
    units = prose_units('!!! note "Heads up"\n    The body is prose.\n')
    assert units == ["The body is prose."]


def test_a_page_with_a_long_sentence_and_an_em_dash_fails() -> None:
    long = " ".join(["word"] * (MAX_SENTENCE_WORDS + 1)) + "."
    problems = check_page(f"{long}\n\nThis has a dash — here.\n", path="x.md")
    assert {p.kind for p in problems} >= {"long-sentence", "em-dash"}


def test_an_average_over_the_limit_is_flagged() -> None:
    sentence = " ".join(["Word"] * (MAX_AVERAGE_WORDS + 1)) + "."
    problems = check_page(f"{sentence} {sentence}\n", path="x.md")
    assert [p.kind for p in problems] == ["average"]

    short = " ".join(["Word"] * MAX_AVERAGE_WORDS) + "."
    assert not check_page(f"{short} {short}\n", path="x.md")


def test_jargon_must_link_to_the_glossary() -> None:
    problems = check_page("Each lens runs on its own.\n", path="docs/how-to/x.md")
    assert any(p.kind == "glossary" and "lens" in p.detail for p in problems)

    linked = "Each [lens](../reference/glossary.md#lens) runs on its own.\n"
    assert not check_page(linked, path="docs/how-to/x.md")


def test_a_glossary_link_inside_a_code_block_does_not_count() -> None:
    page = "Each lens runs on its own.\n\n```md\n[lens](../reference/glossary.md#lens)\n```\n"
    problems = check_page(page, path="docs/how-to/x.md")
    assert any(p.kind == "glossary" and "lens" in p.detail for p in problems)


def test_a_link_to_a_longer_entry_does_not_count() -> None:
    """#lens-pack is its own entry; it does not explain "lens"."""
    page = "Each lens runs on its own; see [lens packs](../reference/glossary.md#lens-pack).\n"
    problems = check_page(page, path="docs/how-to/x.md")
    assert any(p.kind == "glossary" and "#lens" in p.detail for p in problems)


def test_the_glossary_page_needs_no_links_to_itself() -> None:
    assert not check_page("A lens is one focused question.\n", path="docs/reference/glossary.md")


# --- the gate ----------------------------------------------------------------------


@pytest.mark.parametrize("page", [p for p in _PAGES if p not in NOT_YET_PASSING])
def test_rewritten_pages_read_plainly(page: str) -> None:
    text = (_ROOT / page).read_text(encoding="utf-8")
    problems = check_page(text, path=page)
    assert not problems, "\n".join(str(p) for p in problems)


@pytest.mark.parametrize("page", sorted(NOT_YET_PASSING))
def test_the_not_yet_passing_list_only_shrinks(page: str) -> None:
    """A listed page that already passes must come off the list."""
    assert page in _PAGES, f"{page} no longer exists; remove it from NOT_YET_PASSING"
    text = (_ROOT / page).read_text(encoding="utf-8")
    assert check_page(text, path=page), f"{page} passes now; remove it from NOT_YET_PASSING"


def test_the_glossary_and_style_guide_are_gated() -> None:
    """The pages that set the rules pass them from the day they land."""
    assert set(_PAGES) - NOT_YET_PASSING >= {
        "docs/reference/glossary.md",
        "docs/how-to/write-docs.md",
    }


def test_the_checker_cli_exits_by_result(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(_ROOT / "docs/how-to/write-docs.md")]) == 0
    # A throwaway failing page, so the test outlives NOT_YET_PASSING emptying.
    # Closed before main() reads it: Windows cannot reopen a file still held open.
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", dir=_ROOT, encoding="utf-8", delete=False
    ) as page:
        page.write("This has a dash \u2014 here.\n")
    try:
        assert main([page.name]) == 1
        assert "[em-dash]" in capsys.readouterr().out
    finally:
        Path(page.name).unlink()
    assert main([str(_ROOT / "docs/no-such-page.md")]) == 1
    assert "not a file inside the repository" in capsys.readouterr().err
    assert main([]) == 0
    assert "docs/how-to/write-docs.md" in capsys.readouterr().out


def test_the_style_guide_states_the_checker_limits() -> None:
    """The guide restates the limits for readers; they must match the checker."""
    guide = (_ROOT / "docs/how-to/write-docs.md").read_text(encoding="utf-8")
    assert f"average sentence over {MAX_AVERAGE_WORDS} words" in guide
    assert f"any sentence over {MAX_SENTENCE_WORDS} words" in guide


# --- glossary, tooltips, and the docs build --------------------------------------


def _glossary_anchors() -> set[str]:
    glossary = (_ROOT / "docs/reference/glossary.md").read_text(encoding="utf-8")
    return {
        re.sub(r"[^a-z0-9 -]", "", m.group(1).lower()).strip().replace(" ", "-")
        for m in re.finditer(r"^## (.+)$", glossary, flags=re.MULTILINE)
    }


def test_every_key_term_has_a_glossary_entry() -> None:
    assert set(KEY_TERMS.values()) <= _glossary_anchors()


def test_every_tooltip_abbreviation_has_a_glossary_entry() -> None:
    abbreviations = (_ROOT / "includes/abbreviations.md").read_text(encoding="utf-8")
    terms = re.findall(r"^\*\[([^\]]+)\]:", abbreviations, flags=re.MULTILINE)
    assert terms, "no abbreviations defined"
    anchors = _glossary_anchors()
    for term in terms:
        assert term.lower() in anchors, f"{term} has a tooltip but no glossary entry"


def test_mkdocs_wires_the_glossary_tooltips_and_anchor_checks() -> None:
    mkdocs = (_ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "- abbr" in mkdocs
    assert "includes/abbreviations.md" in mkdocs
    assert "content.tooltips" in mkdocs
    assert "anchors: warn" in mkdocs
    assert "Glossary: reference/glossary.md" in mkdocs
    assert "Write the docs: how-to/write-docs.md" in mkdocs


def test_contributing_points_at_the_style_guide() -> None:
    contributing = (_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "docs/how-to/write-docs.md" in contributing
