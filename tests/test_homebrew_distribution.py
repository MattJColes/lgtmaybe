"""Release-workflow and docs contracts for the Homebrew tap.

The tap slug comes from the tap repo's name: `MattJColes/homebrew-tap` resolves to
the tap `MattJColes/tap`, so the fully-qualified formula is
`MattJColes/tap/lgtmaybe`. These tests pin that name in the publish workflow and
the install docs, because a mismatch between the two only shows up at release time.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"

TAP_REPO = "MattJColes/homebrew-tap"
TAP = "MattJColes/tap"
FORMULA = f"{TAP}/lgtmaybe"


def _workflow(name: str) -> tuple[str, dict]:
    text = (_WORKFLOWS / name).read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def test_homebrew_workflow_targets_the_tap_repo() -> None:
    text, workflow = _workflow("homebrew.yml")
    steps = {step["name"]: step for step in workflow["jobs"]["formula"]["steps"]}
    checkout = steps["Checkout the tap"]

    assert checkout["with"]["repository"] == TAP_REPO
    assert checkout["with"]["token"] == "${{ secrets.HOMEBREW_TAP_TOKEN }}"
    # A tap rename changes only the slug — the formula file keeps its name.
    assert "tap/Formula/lgtmaybe.rb" in text
    assert "homebrew-lgtmaybe" not in text


def test_homebrew_smoke_test_installs_the_generated_formula() -> None:
    _text, workflow = _workflow("homebrew.yml")
    steps = {step["name"]: step for step in workflow["jobs"]["formula"]["steps"]}
    run = steps["Smoke-test that the formula installs and runs"]["run"]

    # The local tap directory name and the install spec must agree: Homebrew derives
    # `local/tap` from a `Library/Taps/local/homebrew-tap` directory.
    assert "Library/Taps/local/homebrew-tap" in run
    assert 'brew install --formula "local/tap/lgtmaybe"' in run


def test_homebrew_docs_use_the_renamed_tap() -> None:
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    guide = (_ROOT / "docs" / "how-to" / "install-the-cli.md").read_text(encoding="utf-8")
    tutorial = (_ROOT / "docs" / "tutorial" / "getting-started.md").read_text(encoding="utf-8")

    for text in (readme, guide, tutorial):
        assert f"brew tap {TAP}" in text
        assert f"brew trust {TAP}" in text
        assert "MattJColes/lgtmaybe/lgtmaybe" not in text

    assert f"brew install {FORMULA}" in guide
    assert f"https://github.com/{TAP_REPO}" in guide
    # Anyone already on the old tap needs the one-time switch.
    assert "brew untap MattJColes/lgtmaybe" in guide


def test_releasing_guide_names_the_tap_repo_to_create() -> None:
    guide = (_ROOT / "docs" / "how-to" / "releasing.md").read_text(encoding="utf-8")

    assert TAP_REPO in guide
    assert "HOMEBREW_TAP_TOKEN" in guide
    assert f"brew install {FORMULA}" in guide
    # The guide may name the old slug to explain why the repo isn't called that,
    # but it must never point at it.
    assert "MattJColes/homebrew-lgtmaybe" not in guide
