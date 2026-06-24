"""Tests for suppress.py — drop findings by fingerprint or inline pragma."""

from __future__ import annotations

from lgtmaybe.core.models import Provider, ReviewConfig, ReviewFinding, Severity
from lgtmaybe.engine.suppress import apply_suppressions, is_suppressed
from lgtmaybe.github.rest_gateway import finding_fingerprint

_CFG = ReviewConfig(provider=Provider.ollama, model="llama3")


def _finding(path: str = "a.py", line: int = 2, title: str = "Possible bug") -> ReviewFinding:
    return ReviewFinding(
        path=path, line=line, severity=Severity.medium, title=title, body="detail"
    )


def test_config_fingerprint_suppresses() -> None:
    f = _finding()
    fp = finding_fingerprint(f.path, f.title)
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", ignore_fingerprints=[fp])

    assert is_suppressed(f, cfg, {}) is True


def test_inline_pragma_on_the_line_suppresses() -> None:
    f = _finding(line=2)
    contents = {"a.py": "import os\nx = eval(data)  # lgtmaybe: ignore\ny = 1\n"}

    assert is_suppressed(f, _CFG, contents) is True


def test_pragma_on_preceding_line_suppresses() -> None:
    f = _finding(line=3)
    contents = {"a.py": "import os\n# lgtmaybe: ignore\nx = eval(data)\n"}

    assert is_suppressed(f, _CFG, contents) is True


def test_unrelated_finding_not_suppressed() -> None:
    f = _finding(line=2, title="Different issue")
    contents = {"a.py": "import os\nx = eval(data)\ny = 1\n"}

    assert is_suppressed(f, _CFG, contents) is False


def test_pragma_is_case_insensitive() -> None:
    f = _finding(line=2)
    contents = {"a.py": "import os\nx = eval(data)  # LGTMAYBE: IGNORE\n"}

    assert is_suppressed(f, _CFG, contents) is True


def test_out_of_bounds_line_is_not_an_error() -> None:
    f = _finding(line=999)
    contents = {"a.py": "import os\nx = 1\n"}

    assert is_suppressed(f, _CFG, contents) is False


def test_apply_suppressions_filters_only_suppressed() -> None:
    keep = _finding(line=2, title="Real bug")
    drop = _finding(line=3, title="Ignored")
    contents = {"a.py": "import os\nx = real_bug()\ny = ignored()  # lgtmaybe: ignore\n"}

    out = apply_suppressions([keep, drop], _CFG, contents)

    assert keep in out
    assert drop not in out
