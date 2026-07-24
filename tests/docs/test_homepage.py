import re
from pathlib import Path


def test_homepage_shows_change_diagram_example() -> None:
    homepage = Path("docs/index.md").read_text(encoding="utf-8")

    assert "```mermaid\nC4Container" in homepage
    assert "(changed)" in homepage
    assert "(new)" in homepage
    assert 'Container(queue, "Order events"' in homepage
    assert 'Container(worker, "Notification worker"' in homepage
    assert 'System_Ext(email, "Email provider"' in homepage
    assert "how-to/generate-a-change-diagram.md" in homepage


def test_homepage_overview_is_concise_without_hiding_features() -> None:
    homepage = Path("docs/index.md").read_text(encoding="utf-8")
    overview = homepage.split("## Start here", 1)[0].casefold()

    assert len(re.findall(r"\b[\w'-]+\b", overview)) <= 400
    for feature in (
        "correctness",
        "security",
        "performance",
        "complexity",
        "tests",
        "documentation",
        "deprecations",
        "intent",
        "ponytail",
    ):
        assert feature in overview
