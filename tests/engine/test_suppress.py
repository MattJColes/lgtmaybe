"""Tests for suppress.py — drop findings by fingerprint or inline pragma."""

from __future__ import annotations

from lgtmaybe.core.findings import finding_fingerprint
from lgtmaybe.core.models import Provider, ReviewConfig, ReviewFinding, Severity
from lgtmaybe.engine.suppress import apply_suppressions

_CFG = ReviewConfig(provider=Provider.ollama, model="llama3")


def _finding(path: str = "a.py", line: int = 2, title: str = "Possible bug") -> ReviewFinding:
    return ReviewFinding(path=path, line=line, severity=Severity.medium, title=title, body="detail")


def test_config_fingerprint_suppresses() -> None:
    f = _finding()
    fp = finding_fingerprint(f.path, f.title)
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", ignore_fingerprints=[fp])

    assert apply_suppressions([f], cfg, {}) == []


def test_downvoted_fingerprint_fed_in_is_suppressed() -> None:
    """A fingerprint learned from a 👎 reaction is merged into
    ignore_fingerprints exactly as run_review does (model_copy update), and the
    matching finding is then dropped by the shared suppression path."""
    f = _finding(title="Downvoted nit")
    downvoted = finding_fingerprint(f.path, f.title)
    cfg = _CFG.model_copy(update={"ignore_fingerprints": [*_CFG.ignore_fingerprints, downvoted]})

    assert apply_suppressions([f], cfg, {}) == []


def test_inline_pragma_on_the_line_suppresses() -> None:
    f = _finding(line=2)
    contents = {"a.py": "import os\nx = eval(data)  # lgtmaybe: ignore\ny = 1\n"}

    assert apply_suppressions([f], _CFG, contents) == []


def test_pragma_on_preceding_line_suppresses() -> None:
    f = _finding(line=3)
    contents = {"a.py": "import os\n# lgtmaybe: ignore\nx = eval(data)\n"}

    assert apply_suppressions([f], _CFG, contents) == []


def test_unrelated_finding_not_suppressed() -> None:
    f = _finding(line=2, title="Different issue")
    contents = {"a.py": "import os\nx = eval(data)\ny = 1\n"}

    assert apply_suppressions([f], _CFG, contents) == [f]


def test_pragma_is_case_insensitive() -> None:
    f = _finding(line=2)
    contents = {"a.py": "import os\nx = eval(data)  # LGTMAYBE: IGNORE\n"}

    assert apply_suppressions([f], _CFG, contents) == []


def test_out_of_bounds_line_is_not_an_error() -> None:
    f = _finding(line=999)
    contents = {"a.py": "import os\nx = 1\n"}

    assert apply_suppressions([f], _CFG, contents) == [f]


def test_apply_suppressions_filters_only_suppressed() -> None:
    keep = _finding(line=2, title="Real bug")
    drop = _finding(line=3, title="Ignored")
    contents = {"a.py": "import os\nx = real_bug()\ny = ignored()  # lgtmaybe: ignore\n"}

    out = apply_suppressions([keep, drop], _CFG, contents)

    assert keep in out
    assert drop not in out


class _CountingStr(str):
    """A str that tallies how many times it is split — to prove the hot path
    splits each file's text once, not once per finding on that file."""

    splits = 0

    def split(self, *args: object, **kwargs: object) -> list[str]:  # type: ignore[override]
        type(self).splits += 1
        return super().split(*args, **kwargs)  # type: ignore[arg-type]


def test_apply_suppressions_splits_each_file_once() -> None:
    text = _CountingStr("import os\nx = 1\ny = 2\nz = 3\n")
    _CountingStr.splits = 0
    findings = [
        _finding(line=2, title="One"),
        _finding(line=3, title="Two"),
        _finding(line=4, title="Three"),
    ]

    apply_suppressions(findings, _CFG, {"a.py": text})

    assert _CountingStr.splits == 1
