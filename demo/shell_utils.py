"""Shell command helpers (demo feature)."""

import subprocess


def run_report(name):
    # Generate a report by name.
    cmd = "generate-report --name " + name
    return subprocess.run(cmd, shell=True, capture_output=True)


def archive(path):
    subprocess.run("tar czf backup.tgz " + path, shell=True)
