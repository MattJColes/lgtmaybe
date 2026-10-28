"""Scan the exact release binary with Defender and retain diagnostic evidence.

Requires Windows and an elevated runner with Defender enabled. This uses the
documented non-remediating custom scan: a detection or scanner error must block
publication, and a successful scan must leave the bytes unchanged. This is a
local antivirus check; winget's installation tests still run separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

# Hosted Windows images can ship with the Defender service stopped.
_DEFENDER_REFRESH = """
$ErrorActionPreference = 'Stop'
$service = Get-Service WinDefend -ErrorAction Stop
if ($service.Status -ne 'Running') {
    Set-Service WinDefend -StartupType Automatic -ErrorAction Stop
    Start-Service WinDefend -ErrorAction Stop
}
Set-MpPreference -DisableRealtimeMonitoring $false -ErrorAction Stop
Update-MpSignature -ErrorAction Stop
Get-MpComputerStatus -ErrorAction Stop |
    Select-Object AntivirusEnabled, RealTimeProtectionEnabled, AMRunningMode,
        AMProductVersion, AMEngineVersion, AntivirusSignatureVersion,
        AntivirusSignatureLastUpdated |
    ConvertTo-Json -Compress
"""


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _run(args: list[str], log: Path, timeout: int) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        log.write_bytes((error.stdout or b"") + (error.stderr or b""))
        raise
    log.write_bytes(result.stdout + result.stderr)
    if result.returncode != 0:
        # Always expose failures in Actions as well as the downloadable logs.
        # ASCII escaping also works on Windows consoles with a legacy encoding.
        print(log.read_bytes().decode("ascii", errors="backslashreplace"))
    return result


def _refresh(args: list[str], report_dir: Path) -> subprocess.CompletedProcess:
    result = _run(args, report_dir / "update.log", timeout=120)
    for attempt in range(2, 4):
        if result.returncode == 0:
            break
        time.sleep(5)
        result = _run(args, report_dir / f"update-{attempt}.log", timeout=120)
    return result


def _scanner() -> Path:
    # Prefer the serviced platform over the older inbox executable.
    platform = Path(os.environ["ProgramData"]) / "Microsoft/Windows Defender/Platform"
    candidates = sorted(platform.glob("*/MpCmdRun.exe"), reverse=True)
    candidates.append(Path(os.environ["ProgramFiles"]) / "Windows Defender/MpCmdRun.exe")
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("Microsoft Defender's MpCmdRun.exe was not found")


def scan_executable(executable: Path, report_dir: Path, asset_url: str = "") -> int:
    report_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {
        "file": executable.name,
        "asset_url": asset_url,
        "checked_at": datetime.now(UTC).isoformat(),
        "verdict": "failed",
    }
    try:
        executable = executable.resolve(strict=True)
        result["sha256"] = _hash(executable)
        update = _refresh(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _DEFENDER_REFRESH,
            ],
            report_dir,
        )
        if update.returncode != 0:
            raise RuntimeError(
                f"Defender signature update/status failed (exit {update.returncode})"
            )
        status = json.loads(update.stdout.decode("utf-8-sig"))
        result["defender"] = status
        if (
            not isinstance(status, dict)
            or status.get("AntivirusEnabled") is not True
            or status.get("RealTimeProtectionEnabled") is not True
            or status.get("AMRunningMode") != "Normal"
        ):
            raise RuntimeError("Defender and real-time protection must be enabled in Normal mode")
        scan = _run(
            [
                str(_scanner()),
                "-Scan",
                "-ScanType",
                "3",
                "-File",
                str(executable),
                "-DisableRemediation",
            ],
            report_dir / "scan.log",
            timeout=600,
        )
        result["scan_exit_code"] = scan.returncode
        if scan.returncode != 0:
            raise RuntimeError(
                f"Defender detected a threat or failed to scan (exit {scan.returncode})"
            )
        if not executable.is_file() or _hash(executable) != result["sha256"]:
            raise RuntimeError("The executable changed or disappeared during the scan")
        result["verdict"] = "passed"
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        result["error"] = str(error)

    (report_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    report = (
        f"## Windows Defender check: {result['verdict']}\n\n"
        f"File: `{result['file']}`\n\n"
        f"SHA-256: `{result.get('sha256', 'unavailable')}`\n\n"
        f"Release asset: {asset_url or 'not yet published'}\n\n"
        f"Details: {result.get('error', 'Custom scan completed; executable bytes unchanged.')}\n\n"
        "Logs and Defender engine/signature versions are in the defender-report artefact.\n\n"
        "A failed check blocks publication or winget submission. Inspect scan.log for the "
        "detection or scanner error, fix the build, and rerun the check. "
        "This check does not reproduce all winget installation tests.\n"
    )
    (report_dir / "report.md").write_text(report, encoding="utf-8")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(report)
    print(report)
    return 0 if result["verdict"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--report-dir", type=Path, default=Path("defender-report"))
    parser.add_argument("--asset-url", default="")
    args = parser.parse_args()
    raise SystemExit(scan_executable(args.executable, args.report_dir, args.asset_url))
