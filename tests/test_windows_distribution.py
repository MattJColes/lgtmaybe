"""Release-workflow contracts for the Windows executable and winget chain."""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"


def _workflow(name: str) -> tuple[str, dict]:
    text = (_WORKFLOWS / name).read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def test_main_ci_runs_only_minimum_python_on_linux_and_windows() -> None:
    _text, workflow = _workflow("ci.yml")
    job = workflow["jobs"]["test"]
    setup_uv = next(step for step in job["steps"] if step.get("uses") == "astral-sh/setup-uv@v7")

    assert job["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "windows-latest"],
    }
    assert setup_uv["with"]["python-version"] == "3.11"


def test_windows_exe_workflow_builds_smokes_and_uploads() -> None:
    text, workflow = _workflow("windows-exe.yml")
    job = workflow["jobs"]["build"]
    runs = "\n".join(str(step.get("run", "")) for step in job["steps"] if isinstance(step, dict))

    assert "workflow_call:" in text
    assert "workflow_dispatch:" in text
    assert job["runs-on"] == "windows-latest"
    assert "uv run pyinstaller packaging/pyinstaller/lgtmaybe.spec" in runs
    for command in ("--help", "config path", "help review"):
        assert command in runs
    assert "gh release upload" in runs
    assert runs.count('--repo "$env:GITHUB_REPOSITORY"') == 2
    assert "gh release upload failed with exit code $LASTEXITCODE" in runs
    assert "windows-x86_64.exe" in runs
    assert ".Length" in runs


def test_winget_workflow_updates_the_portable_package() -> None:
    text, workflow = _workflow("winget.yml")
    job = workflow["jobs"]["publish"]
    runs = "\n".join(str(step.get("run", "")) for step in job["steps"] if isinstance(step, dict))

    assert "workflow_call:" in text
    assert "workflow_dispatch:" in text
    assert job["runs-on"] == "windows-latest"
    assert "MattJColes.lgtmaybe" in runs
    assert "wingetcreate update" in runs
    assert "WINGET_TOKEN" in text
    assert "windows-x86_64.exe" in runs


def test_winget_docs_cover_installation_lifecycle() -> None:
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    guide = (_ROOT / "docs" / "how-to" / "install-the-cli.md").read_text(encoding="utf-8")
    install = "winget install --id MattJColes.lgtmaybe --exact"

    assert install in readme
    assert install in guide
    for text in (
        "Windows x86_64 (64-bit)",
        "lgtmaybe --help",
        "winget upgrade --id MattJColes.lgtmaybe --exact",
        "winget uninstall --id MattJColes.lgtmaybe --exact",
        "winget source update",
    ):
        assert text in guide


def test_release_please_sequences_windows_exe_before_winget() -> None:
    _text, workflow = _workflow("release-please.yml")
    jobs = workflow["jobs"]

    assert jobs["windows-exe"]["needs"] == "release-please"
    assert jobs["windows-exe"]["uses"] == "./.github/workflows/windows-exe.yml"
    assert "secrets" not in jobs["windows-exe"]
    assert jobs["winget"]["needs"] == ["release-please", "windows-exe"]
    assert jobs["winget"]["uses"] == "./.github/workflows/winget.yml"
    assert jobs["winget"]["secrets"] == {
        "WINGET_TOKEN": "${{ secrets.WINGET_TOKEN }}",
    }
