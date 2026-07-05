"""Finding suppression: drop findings a team has marked known-fine.

Two suppression channels, both deterministic and applied before reflection so a
suppressed finding costs no reflection tokens:

- **Config fingerprint** — ``ReviewConfig.ignore_fingerprints`` lists
  ``finding_fingerprint(path, title)`` ids a team has permanently dismissed.
- **Inline pragma** — a ``# lgtmaybe: ignore`` comment on the flagged line (or
  the line immediately above it) in the file's head text suppresses that finding,
  so a developer can silence a known-fine pattern at the source.
"""

from __future__ import annotations

import re

from lgtmaybe.core.models import ReviewConfig, ReviewFinding
from lgtmaybe.github.rest_gateway import finding_fingerprint

# A `# lgtmaybe: ignore` pragma anywhere in a line (after a `#` comment marker).
# Case-insensitive so `# LGTMAYBE: IGNORE` works too.
_PRAGMA = re.compile(r"#\s*lgtmaybe:\s*ignore\b", re.IGNORECASE)


def _is_suppressed(finding: ReviewFinding, cfg: ReviewConfig, lines: list[str] | None) -> bool:
    """Whether *finding* is suppressed, against a file's already-split lines (None if no text).

    Split out so ``apply_suppressions`` can split each file's head text once and
    reuse it across every finding on that file, rather than re-splitting per
    finding (the file's text is unchanged within a run).
    """
    if finding_fingerprint(finding.path, finding.title) in cfg.ignore_fingerprints:
        return True
    if not lines:
        return False
    # The finding's own line and the one just above it (1-based -> 0-based index).
    for idx in (finding.line - 1, finding.line - 2):
        if 0 <= idx < len(lines) and _PRAGMA.search(lines[idx]):
            return True
    return False


def apply_suppressions(
    findings: list[ReviewFinding], cfg: ReviewConfig, file_contents: dict[str, str]
) -> list[ReviewFinding]:
    """Return *findings* with the suppressed ones removed (order preserved).

    Each file's head text is split into lines once (lazily, only for paths a
    finding actually lands on) and reused across that file's findings.
    """
    lines_cache: dict[str, list[str]] = {}

    def lines_for(path: str) -> list[str]:
        cached = lines_cache.get(path)
        if cached is None:
            text = file_contents.get(path)
            cached = text.split("\n") if text else []
            lines_cache[path] = cached
        return cached

    return [f for f in findings if not _is_suppressed(f, cfg, lines_for(f.path))]
