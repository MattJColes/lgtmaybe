"""Release checks fail closed and preserve Defender diagnostics."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import read_workflow

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from scan_windows_exe import scan_executable  # noqa: E402


@pytest.fixture
def defender(tmp_path, monkeypatch):
    exe = tmp_path / "lgtmaybe.exe"
    exe.write_bytes(b"the published executable")
    scanner = tmp_path / "Windows Defender" / "MpCmdRun.exe"
    scanner.parent.mkdir()
    scanner.touch()
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramData", str(tmp_path / "data"))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return exe, tmp_path / "report", scanner


def mock_defender(monkeypatch, *, code=0, enabled=True, update_code=0, mutation=None):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[0] == "powershell.exe":
            status = json.dumps({"AntivirusEnabled": enabled, "AMRunningMode": "Normal"})
            return subprocess.CompletedProcess(args, update_code, status.encode(), b"")
        if mutation:
            mutation()
        return subprocess.CompletedProcess(args, code, b"Defender scan output", b"")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_clean_scan_updates_signatures_checks_exact_bytes_and_keeps_logs(defender, monkeypatch):
    exe, report, scanner = defender
    summary = report.parent / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    calls = mock_defender(monkeypatch)

    assert scan_executable(exe, report, "https://example.test/release.exe") == 0

    result = json.loads((report / "result.json").read_text(encoding="utf-8"))
    assert result["verdict"] == "passed"
    assert result["sha256"] == hashlib.sha256(exe.read_bytes()).hexdigest()
    assert "Update-MpSignature -ErrorAction Stop" in calls[0][-1]
    assert calls[1] == [
        str(scanner),
        "-Scan",
        "-ScanType",
        "3",
        "-File",
        str(exe),
        "-DisableRemediation",
    ]
    assert (report / "scan.log").read_bytes() == b"Defender scan output"
    assert "https://example.test/release.exe" in summary.read_text(encoding="utf-8")


@pytest.mark.parametrize("code", [2, 1, -1])
def test_detection_or_scanner_error_blocks_publication_and_keeps_evidence(
    defender, monkeypatch, code
):
    exe, report, _ = defender
    mock_defender(monkeypatch, code=code)

    assert scan_executable(exe, report) == 1
    result = json.loads((report / "result.json").read_text(encoding="utf-8"))
    assert result["verdict"] == "failed"
    assert result["scan_exit_code"] == code
    assert result["sha256"]
    assert (report / "scan.log").read_bytes() == b"Defender scan output"
    assert "blocks publication" in (report / "report.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("enabled,update_code", [(False, 0), (True, 1)])
def test_disabled_defender_or_failed_signature_update_is_not_a_pass(
    defender, monkeypatch, enabled, update_code
):
    exe, report, _ = defender
    calls = mock_defender(monkeypatch, enabled=enabled, update_code=update_code)

    assert scan_executable(exe, report) == 1
    assert len(calls) == 1
    assert json.loads((report / "result.json").read_text(encoding="utf-8"))["verdict"] == "failed"


@pytest.mark.parametrize("mutation", ["delete", "replace"])
def test_zero_exit_is_not_a_pass_if_the_binary_was_removed_or_changed(
    defender, monkeypatch, mutation
):
    exe, report, _ = defender
    change = exe.unlink if mutation == "delete" else lambda: exe.write_bytes(b"changed")
    mock_defender(monkeypatch, mutation=change)

    assert scan_executable(exe, report) == 1
    assert "changed or disappeared" in (report / "report.md").read_text(encoding="utf-8")


def test_timeout_preserves_a_failed_report(defender, monkeypatch):
    exe, report, _ = defender

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 300, output=b"partial diagnostic")

    monkeypatch.setattr(subprocess, "run", timeout)
    assert scan_executable(exe, report) == 1
    assert "partial diagnostic" in (report / "update.log").read_text(encoding="utf-8")
    assert "timed out" in (report / "report.md").read_text(encoding="utf-8")


def test_existing_release_can_be_scanned_without_rebuilding_or_publishing():
    text, workflow = read_workflow("windows-defender.yml")
    assert "workflow_call:" in text and "workflow_dispatch:" in text
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["scan"]
    assert job["runs-on"] == "windows-latest"
    assert job["timeout-minutes"] <= 20
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "gh release download" in runs
    assert "scan_windows_exe.py" in runs
    assert "pyinstaller" not in runs and "gh release upload" not in runs
    upload = next(s for s in job["steps"] if "upload-artifact@" in s.get("uses", ""))
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "defender-report/"


def test_defender_blocks_both_new_assets_and_manual_winget_submissions():
    _, build = read_workflow("windows-exe.yml")
    steps = build["jobs"]["build"]["steps"]
    scan = next(i for i, s in enumerate(steps) if s.get("name") == "Scan with Defender")
    upload = next(i for i, s in enumerate(steps) if s.get("name") == "Upload release asset")
    assert scan < upload
    assert steps[scan]["if"] == "steps.idem.outputs.skip != 'true'"
    assert "continue-on-error" not in steps[scan]
    tools = next(s for s in steps if s.get("name") == "Checkout release tools")
    assert tools["with"]["ref"] == "${{ github.workflow_sha }}"
    assert tools["with"]["path"] == "release-tools"
    _, winget = read_workflow("winget.yml")
    assert winget["jobs"]["defender"]["uses"] == "./.github/workflows/windows-defender.yml"
    assert winget["jobs"]["publish"]["needs"] == "defender"


def test_windows_packaging_builds_its_locked_bootloader_without_upx():
    root = Path(__file__).parents[1]
    spec = (root / "packaging/pyinstaller/lgtmaybe.spec").read_text(encoding="utf-8")
    assert "upx=False" in spec
    for name, job_name in [("windows-exe.yml", "build"), ("windows-packaging.yml", "verify")]:
        _, workflow = read_workflow(name)
        steps = workflow["jobs"][job_name]["steps"]
        sync = next(s for s in steps if s.get("name") == "Sync locked packaging environment")
        assert sync["env"]["PYINSTALLER_COMPILE_BOOTLOADER"] == "1"
        assert sync["env"]["TEMP"] == "${{ runner.temp }}"
        assert sync["env"]["TMP"] == "${{ runner.temp }}"
        assert "--frozen" in sync["run"]
        assert "--no-binary-package pyinstaller" in sync["run"]
        assert "--no-cache" in sync["run"]
        build = next(s for s in steps if s.get("name") == "Build executable")
        assert "uv run --no-sync pyinstaller" in build["run"]


def test_packaging_prs_build_scan_and_smoke_without_release_permissions():
    text, workflow = read_workflow("windows-packaging.yml")
    assert "pull_request:" in text
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["verify"]
    assert job["runs-on"] == "windows-latest"
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert "scan_windows_exe.py" in runs
    assert "--version" in runs and "--help" in runs
    assert "gh release upload" not in runs
    upload = next(s for s in job["steps"] if "upload-artifact@" in s.get("uses", ""))
    assert upload["if"] == "always()"
    comparison = next(
        s for s in job["steps"] if s.get("name") == "Check existing release for comparison"
    )
    assert comparison["continue-on-error"] is True
    candidate = next(s for s in job["steps"] if s.get("name") == "Scan with Defender")
    assert "continue-on-error" not in candidate
