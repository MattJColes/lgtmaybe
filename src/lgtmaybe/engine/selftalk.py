"""A model's note to itself is not a finding.

A model that stumbles mid-answer sometimes writes itself a note — *"the corrupted
text above is not instructions to follow — it is corrupted output to be
discarded. Produce a clean, valid JSON findings object … Output valid JSON
only, no prose, no trailing junk"* — and starts the answer again. When that note
lands inside a JSON string the parser has no reason to reject it, so it reaches
the pull request as the body of an inline comment: imperative text addressed to
a model, posted where every agent that reads review threads as instructions
will read it. Measured on a production round (a `z-ai/glm-5.3-flash` code-health
call): one finding's body carried the note mid-sentence, and a second "finding"
was nothing but the note, with ``findings`` for a path.

The review prompt tells the model the diff is data, not instructions. Its output
gets the same rule on the way out: a finding whose prose is the model talking to
itself about its output format is corrupted output, and the note itself says to
discard it. The engine drops such findings before they are merged, counts them
per lens, and names the count in the summary — a dropped finding is never
silent, because the genuine half of a corrupted one is lost with it.

Detection is deterministic and needs no model. A note has two halves — it
refers to a **broken attempt** ("corrupted output", "formatting error",
"meta-text", "not instructions to follow", "discard the … output") and it names
the **shape it must produce** ("valid JSON", "findings object", "no prose",
"JSON only") — and a finding is dropped only when its prose carries BOTH. Either
half alone is plausible in a genuine finding about someone else's parser
("returns valid JSON", "a malformed findings object"), and dropping a real
finding is the failure this module exists to avoid.
"""

from __future__ import annotations

import re

from lgtmaybe.core.models import ReviewFinding

# The note describing the reply it sits in as a broken attempt.
_BROKEN_ATTEMPT = re.compile(
    r"\b(?:corrupted|garbled)\s+(?:text|output|response|reply|json)\b"
    r"|\bmeta-?text\b"
    r"|\bnot\s+instructions\s+to\s+follow\b"
    r"|\bformatting\s+error\b"
    r"|\bdiscard(?:ed|ing)?\s+(?:all\s+|the\s+|this\s+)?(?:corrupted\s+|garbled\s+|previous\s+|above\s+)?"
    r"(?:output|text|response|attempt)\b",
    re.IGNORECASE,
)

# The note naming the output shape the reply was supposed to take.
_REQUIRED_SHAPE = re.compile(
    r"\bvalid\s+JSON\b"
    r"|\bfindings\s+(?:object|array|list)\b"
    r"|\bno\s+prose\b"
    r"|\btrailing\s+junk\b"
    r"|\bJSON\s+only\b",
    re.IGNORECASE,
)


def _prose(finding: ReviewFinding) -> str:
    """Every model-authored text field, joined — a note can land in any of them."""
    return "\n".join(
        part
        for part in (finding.title, finding.body, finding.failure_scenario, finding.suggestion)
        if part
    )


def is_self_talk(finding: ReviewFinding) -> bool:
    """Whether *finding*'s prose is the model's note to itself about its output.

    Both halves of the note must be present — see the module docstring for why
    one is not enough.
    """
    text = _prose(finding)
    return bool(_BROKEN_ATTEMPT.search(text)) and bool(_REQUIRED_SHAPE.search(text))


def drop_self_talk(
    findings: list[ReviewFinding],
) -> tuple[list[ReviewFinding], list[ReviewFinding]]:
    """Partition *findings* into ``(kept, dropped)``, input order preserved."""
    kept: list[ReviewFinding] = []
    dropped: list[ReviewFinding] = []
    for finding in findings:
        (dropped if is_self_talk(finding) else kept).append(finding)
    return kept, dropped
