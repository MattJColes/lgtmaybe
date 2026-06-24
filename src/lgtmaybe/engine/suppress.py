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


def is_suppressed(
    finding: ReviewFinding, cfg: ReviewConfig, file_contents: dict[str, str]
) -> bool:
    """Whether *finding* should be dropped before reflection and posting.

    Suppressed when its ``finding_fingerprint(path, title)`` is in
    ``cfg.ignore_fingerprints``, OR when the finding's own line — or the line
    immediately above it — in ``file_contents[finding.path]`` carries a
    ``# lgtmaybe: ignore`` pragma. The line lookup is 1-based and bounds-checked,
    so a finding whose line is past the file (or whose file has no fetched text)
    simply isn't pragma-suppressed.
    """
    if finding_fingerprint(finding.path, finding.title) in cfg.ignore_fingerprints:
        return True

    text = file_contents.get(finding.path)
    if not text:
        return False
    lines = text.split("\n")
    # The finding's own line and the one just above it (1-based -> 0-based index).
    for idx in (finding.line - 1, finding.line - 2):
        if 0 <= idx < len(lines) and _PRAGMA.search(lines[idx]):
            return True
    return False


def apply_suppressions(
    findings: list[ReviewFinding], cfg: ReviewConfig, file_contents: dict[str, str]
) -> list[ReviewFinding]:
    """Return *findings* with the suppressed ones removed (order preserved)."""
    return [f for f in findings if not is_suppressed(f, cfg, file_contents)]
