"""Stable hidden ids that let a re-run recognise a finding it already made.

Host-neutral, and in ``core`` for that reason: every forge adapter embeds these
ids in the comments it posts so the next run can tell "already reported" from
"new", and "still true" from "fixed". Neither id contains model prose, so both
are safe to write into a comment body verbatim.
"""

from __future__ import annotations

import hashlib

from .models import ReviewFinding


def finding_fingerprint(path: str, title: str) -> str:
    """Stable short id for a finding's identity (its file and what it flags).

    Used to recognise the same finding across review runs: if a fingerprint that
    opened a conversation is no longer produced, that conversation is a candidate
    to auto-resolve. Only the path and title feed the hash (never model prose),
    so the marker is safe to embed in a comment body verbatim.
    """
    digest = hashlib.sha256(f"{path}\n{title.strip().lower()}".encode())
    return digest.hexdigest()[:12]


def finding_identity(finding: ReviewFinding) -> str:
    """Stable short id for *what* a finding is about, independent of how it reads.

    ``finding_fingerprint`` hashes the title, and the title is model prose: ask a
    model to review the same diff twice and it flags the same problem in different
    words, producing a different fingerprint each run. Dedupe keyed on that alone
    cannot survive a re-run, so this is the key that can — built only from fields
    the model does not paraphrase:

    - ``path`` — the file.
    - ``category`` — the lens that raised it (engine-stamped, a fixed vocabulary),
      so two different concerns about one line stay distinct.
    - ``side`` — whether the finding is on the old or new side of the diff.
    - ``anchor`` — the verbatim source line the finding is about. Copied out of the
      diff rather than composed, so it is code, not prose. It also absorbs line
      drift: the model miscounts diff line numbers (the reason anchors exist at
      all), and the same flagged line reported at 428 on one run and 501 on the
      next is one finding, not two.

    With no anchor there is nothing to key on but the reported line, so identity
    falls back to it — still prose-free, just less tolerant of a miscount.
    """
    # Collapse whitespace runs so re-indentation of the same statement doesn't read
    # as a different line; keep case, because code is case-sensitive.
    anchor = " ".join(finding.anchor.split()) if finding.anchor else ""
    locator = anchor or f"L{finding.line}"
    digest = hashlib.sha256(
        f"{finding.path}\n{finding.category or ''}\n{finding.side}\n{locator}".encode()
    )
    return digest.hexdigest()[:12]
