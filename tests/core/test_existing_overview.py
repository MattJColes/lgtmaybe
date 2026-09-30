"""Spotting a change overview someone else already put on the pull request.

The automatic overview is one more comment on every PR. When the author's own
description, or another tool's walkthrough, already diagrams the change, a second
one is noise — so the Action looks first and stands back. The check is
host-neutral: every forge hands it the same description + (author, body) pairs.
"""

from __future__ import annotations

from lgtmaybe.core.comment import find_existing_overview

OWN = "<!-- lgtmaybe-diagram:ollama/llama3 -->"
MERMAID = "## Walkthrough\n\n```mermaid\nsequenceDiagram\n  A->>B: call\n```\n"


def test_a_diagram_in_the_pr_description_counts() -> None:
    assert find_existing_overview(MERMAID, [], own_marker=OWN) == "the PR description"


def test_a_diagram_in_someone_elses_comment_counts() -> None:
    found = find_existing_overview(
        "Fixes the thing.", [("coderabbitai[bot]", MERMAID)], own_marker=OWN
    )
    assert found == "a comment by `coderabbitai[bot]`"


def test_nothing_diagrammed_means_nothing_found() -> None:
    comments = [("alice", "LGTM, but see `mermaid` docs"), ("bob", "```python\nx = 1\n```")]
    assert find_existing_overview("Mentions mermaid in prose.", comments, own_marker=OWN) is None


def test_tilde_fences_and_quoted_fences_count() -> None:
    for body in (
        "~~~mermaid\nflowchart LR\n~~~",
        "> ```mermaid\n> flowchart LR\n> ```",
        "```  Mermaid\nflowchart LR\n```",
    ):
        assert find_existing_overview(body, [], own_marker=OWN) == "the PR description", body


def test_a_plantuml_fence_does_not_count() -> None:
    """GitHub and Gitea show PlantUML as source, so it replaces no rendered overview."""
    body = "```plantuml\n@startuml\nA -> B\n@enduml\n```"
    assert find_existing_overview(body, [], own_marker=OWN) is None


def test_our_own_overview_keeps_the_slot() -> None:
    """Once we post an overview we keep refreshing it — a frozen one goes stale."""
    comments = [("coderabbitai[bot]", MERMAID), ("lgtmaybe[bot]", f"{MERMAID}\n{OWN}")]
    assert find_existing_overview(MERMAID, comments, own_marker=OWN) is None


def test_another_lgtmaybe_setups_overview_counts_as_existing() -> None:
    """A second provider/model on the same PR would otherwise post a duplicate."""
    other = f"{MERMAID}\n<!-- lgtmaybe-diagram:openai/gpt-5 -->"
    found = find_existing_overview("", [("github-actions[bot]", other)], own_marker=OWN)
    assert found == "a comment by `github-actions[bot]`"


def test_an_unknown_author_is_still_named_as_a_comment() -> None:
    assert find_existing_overview("", [("", MERMAID)], own_marker=OWN) == "a comment"
