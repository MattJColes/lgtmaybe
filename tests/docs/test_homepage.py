from pathlib import Path


def test_homepage_shows_change_diagram_example() -> None:
    homepage = Path("docs/index.md").read_text(encoding="utf-8")

    assert "```mermaid\nC4Container" in homepage
    assert "(new)" in homepage
    assert "how-to/generate-a-change-diagram.md" in homepage
