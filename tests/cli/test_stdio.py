"""CLI stdio is safe when Windows redirects output through a legacy codec."""

from __future__ import annotations

import io
import sys

import lgtmaybe.cli as cli


def test_utf8_stdio_reconfigures_legacy_windows_stream(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)

    cli._utf8_stdio()
    print("👍 LGTM!", end="")
    stream.flush()

    assert raw.getvalue().decode("utf-8") == "👍 LGTM!"
