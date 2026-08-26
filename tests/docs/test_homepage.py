import re
from pathlib import Path

_ROOT = Path(__file__).parents[2]


def test_homepage_shows_change_diagram_example() -> None:
    homepage = (_ROOT / "docs/index.md").read_text(encoding="utf-8")

    assert "```mermaid\nflowchart LR" in homepage
    assert "(changed)" in homepage
    assert "(new)" in homepage
    assert 'queue["Order events' in homepage
    assert 'worker["Notification worker' in homepage
    assert 'email["Email provider' in homepage
    assert "how-to/generate-a-change-diagram.md" in homepage


def test_homepage_overview_is_concise_without_hiding_features() -> None:
    homepage = (_ROOT / "docs/index.md").read_text(encoding="utf-8")
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


def test_model_selection_guide_is_linked_from_readme_and_docs_nav() -> None:
    guide = "how-to/choose-a-review-model.md"
    guide_text = (_ROOT / "docs" / guide).read_text(encoding="utf-8")
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    mkdocs = (_ROOT / "mkdocs.yml").read_text(encoding="utf-8")

    assert "## Choose a Cloud Model" in guide_text
    assert "## Choose a Local Model" in guide_text
    assert f"docs/{guide}" in readme
    assert f"Choose a review model: {guide}" in mkdocs
