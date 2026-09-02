"""Release-workflow contracts for the Windows executable and winget chain."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import read_workflow

_ROOT = Path(__file__).parent.parent


def test_main_ci_runs_only_minimum_python_on_linux_and_windows() -> None:
    _text, workflow = read_workflow("ci.yml")
    job = workflow["jobs"]["test"]
    setup_uv = next(
        step for step in job["steps"] if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
    )

    assert job["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "windows-latest"],
    }
    assert setup_uv["with"]["python-version"] == "3.11"


def test_windows_exe_workflow_builds_smokes_and_uploads() -> None:
    text, workflow = read_workflow("windows-exe.yml")
    job = workflow["jobs"]["build"]
    runs = "\n".join(str(step.get("run", "")) for step in job["steps"] if isinstance(step, dict))

    assert "workflow_call:" in text
    assert "workflow_dispatch:" in text
    assert job["runs-on"] == "windows-latest"
    assert "uv run pyinstaller packaging/pyinstaller/lgtmaybe.spec" in runs
    for command in ("--help", "config path", "review --help", "--version"):
        assert command in runs
    assert "gh release upload" in runs
    assert runs.count('--repo "$env:GITHUB_REPOSITORY"') == 2
    assert "gh release upload failed with exit code $LASTEXITCODE" in runs
    assert "windows-x86_64.exe" in runs
    assert ".Length" in runs


def test_pyinstaller_spec_ships_the_distribution_metadata() -> None:
    """`lgtmaybe --version` reads the installed distribution's metadata, and a
    frozen executable has none unless the spec copies it in — so without this the
    winget build, the one install that cannot be identified any other way, is
    exactly the one that answers "unknown"."""
    spec = (_ROOT / "packaging" / "pyinstaller" / "lgtmaybe.spec").read_text(encoding="utf-8")

    assert "copy_metadata" in spec
    assert 'copy_metadata("lgtmaybe")' in spec


def test_winget_workflow_updates_the_portable_package() -> None:
    text, workflow = read_workflow("winget.yml")
    job = workflow["jobs"]["publish"]
    steps = {step["name"]: step for step in job["steps"]}
    runs = "\n".join(str(step.get("run", "")) for step in job["steps"] if isinstance(step, dict))

    assert "workflow_call:" in text
    assert "workflow_dispatch:" in text
    assert job["runs-on"] == "windows-latest"
    assert "MattJColes.lgtmaybe" in runs
    assert "wingetcreate update" in runs
    assert "WINGET_TOKEN" in text
    assert "windows-x86_64.exe" in runs
    package_check = steps["Check whether the winget package exists"]
    assert package_check["id"] == "package"
    assert "microsoft/winget-pkgs/contents/manifests/m/MattJColes/lgtmaybe" in package_check["run"]
    # `gh` is preinstalled on windows runners and already drives the release
    # steps in windows-exe.yml — the existence check reuses it rather than
    # hand-rolling the request, so it needs the same token in scope.
    assert "gh api" in package_check["run"]
    assert "$LASTEXITCODE" in package_check["run"]
    assert package_check["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    # Only a 404 means "not published yet" — a 401/403/5xx/timeout must fail the
    # job, not silently skip the submission by reading as a missing package.
    assert "HTTP 404" in package_check["run"]
    assert "throw" in package_check["run"]
    for name in ("Install wingetcreate", "Submit winget update"):
        assert steps[name]["if"] == "steps.package.outputs.exists == 'true'"


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
    _text, workflow = read_workflow("release-please.yml")
    jobs = workflow["jobs"]

    assert jobs["windows-exe"]["needs"] == "release-please"
    assert jobs["windows-exe"]["uses"] == "./.github/workflows/windows-exe.yml"
    assert "secrets" not in jobs["windows-exe"]
    assert jobs["winget"]["needs"] == ["release-please", "windows-exe"]
    assert jobs["winget"]["uses"] == "./.github/workflows/winget.yml"
    assert jobs["winget"]["secrets"] == {
        "WINGET_TOKEN": "${{ secrets.WINGET_TOKEN }}",
    }


def test_release_please_sequences_pypi_before_homebrew() -> None:
    """The brew gate pip-installs the exact release version from PyPI, so it
    must not race the publisher (the 2.1.3 release failed exactly this way)."""
    _text, workflow = read_workflow("release-please.yml")
    jobs = workflow["jobs"]

    assert jobs["homebrew"]["needs"] == ["release-please", "pypi"]
    # And a failed publish must skip the gate rather than push an uninstallable
    # formula: no `always()` in the job's condition.
    assert "always()" not in jobs["homebrew"]["if"]
