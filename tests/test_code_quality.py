"""Deterministic code-quality guards.

These are *factual*, not stylistic — they fail only on things that are
objectively outdated, never on opinion:

- importing any lgtmaybe module must not trigger a DeprecationWarning (i.e. we
  are not calling deprecated stdlib / dependency APIs on an import path);
- the deprecation gate in pyproject must stay wired, so nobody can silently
  drop it.

Newer-version availability and CVE scanning are intentionally *not* here — they
depend on the outside world at time-of-check and so can't be deterministic. They
live in scheduled background tooling (Dependabot + the audit workflow).
"""

from __future__ import annotations

import importlib
import pkgutil
import subprocess
import sys
import textwrap
import tomllib
import warnings
from pathlib import Path

import pytest

import lgtmaybe

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _all_module_names() -> list[str]:
    """Every importable lgtmaybe submodule, minus the executable entrypoint."""
    names = [lgtmaybe.__name__]
    for info in pkgutil.walk_packages(lgtmaybe.__path__, prefix="lgtmaybe."):
        if info.name.endswith(".__main__"):
            continue  # entrypoint module — nothing to assert, avoid argv side effects
        names.append(info.name)
    return names


@pytest.mark.parametrize("module_name", _all_module_names())
def test_module_imports_without_deprecation_warnings(module_name: str) -> None:
    """No lgtmaybe module may use a deprecated API on its import path."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        warnings.simplefilter("error", PendingDeprecationWarning)
        importlib.import_module(module_name)


def test_deprecation_gate_is_configured() -> None:
    """The pyproject deprecation gate must stay in place (don't silently drop it)."""
    cfg = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    filters = cfg["tool"]["pytest"]["ini_options"]["filterwarnings"]
    assert "error::DeprecationWarning" in filters
    assert "error::PendingDeprecationWarning" in filters
    assert "error::EncodingWarning" in filters


def test_no_default_encoding_io() -> None:
    """Owned text boundaries must not depend on the host locale."""
    script = textwrap.dedent(
        """
        import subprocess
        import sys
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        from click.testing import CliRunner

        from lgtmaybe.cli import main
        from lgtmaybe.config.loader import load_config
        from lgtmaybe.config.store import load, save
        from lgtmaybe.core.models import ReviewConfig, StaticAnalysisTool
        from lgtmaybe.engine.astgrep import _default_runner
        from lgtmaybe.engine.boundaries import definition_spans
        from lgtmaybe.engine.static_analysis import _run_tool, _write_corpus
        from lgtmaybe.local import _git
        from scripts.check_spec_drift import run_scan

        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            config = root / "config.yml"
            save({"model": "模型"}, config)
            assert load(config)["model"] == "模型"

            lens = root / "lens.yml"
            lens.write_text(
                "id: windows\\ninstructions: Проверить код.\\n",
                encoding="utf-8",
            )
            config.write_text(
                f"provider: ollama\\nmodel: llama3\\nlens_paths:\\n  - {lens}\\n",
                encoding="utf-8",
            )
            load_config(config_path=config)

            _write_corpus(root / "corpus", {"src/app.py": "print('👍')\\n"})
            definition_spans(
                "def f():\\n    pass\\n",
                "src/app.py",
                runner=lambda *_args: "[]",
                find_binary=lambda: "ast-grep",
            )

            event = root / "event.json"
            event.write_text("{}", encoding="utf-8")
            runner = CliRunner()
            result = runner.invoke(
                main,
                ["comment", "--event-path", str(event), "--config", str(config)],
            )
            assert result.exit_code == 0, result.output
            result = runner.invoke(
                main,
                ["action"],
                env={
                    "GITHUB_EVENT_NAME": "issue_comment",
                    "GITHUB_EVENT_PATH": str(event),
                    "INPUT_CONFIG_PATH": str(config),
                },
            )
            assert result.exit_code == 0, result.output

            _git(root, "--version")
            _default_runner(sys.executable, "", root)
            with patch(
                "lgtmaybe.engine.static_analysis.shutil.which",
                return_value=sys.executable,
            ):
                _run_tool(
                    StaticAnalysisTool.ruff,
                    root,
                    root,
                    ["src/app.py"],
                    ReviewConfig(provider="ollama", model="llama3"),
                )
            try:
                run_scan(sys.executable, "", root)
            except subprocess.CalledProcessError:
                pass
        """
    )
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "warn_default_encoding",
            "-W",
            "error::EncodingWarning",
            "-c",
            script,
        ],
        cwd=_PYPROJECT.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr
