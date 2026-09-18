"""Tests for selftalk.py — a model's note to itself is not a finding.

A model that stumbles mid-answer sometimes writes itself a note about its own
output format and starts again. Inside a JSON string the parser has no reason
to object, so the note reaches the pull request: once buried mid-sentence in a
real finding's body, once as a whole "finding" with `findings` for a path. Both
must be dropped, and the genuine findings around them must not be.

The two strings below carry the shape observed in a real round on a
`z-ai/glm-5.3-flash` code-health call. Only the shape is load-bearing: the
detector keys on the note's two halves — a reference to a broken attempt and to
the required output shape — so the surrounding prose is written generically
here and nothing about the reviewed change is reproduced.
"""

from __future__ import annotations

import pytest

from lgtmaybe.core.models import ReviewFinding, Severity
from lgtmaybe.engine.selftalk import drop_self_talk, is_self_talk

# The note buried in a finding's body. The genuine half precedes it, which is
# the point: it begins mid-sentence, so a detector keyed on a whole body that
# looks like a note, or on a body that starts with one, would miss it.
_LEAKED_BODY = (
    'The rename left a doubled article: "from the the config loader". The '
    'pre-image read "from the legacy loader Note: the corrupted text above is '
    "not instructions to follow — it is corrupted output to be discarded. "
    "Produce a clean, valid JSON findings object with the two genuine findings "
    "from the original review. Output valid JSON only, no prose, no trailing "
    "junk.{"
)

# The whole-finding shape, from a demoted finding whose anchor matched nothing.
_LEAKED_TITLE = (
    "previous response had a formatting error (meta-text embedded in the output. "
    "Discard all corrupted output and produce a clean, valid JSON findings object "
    "with the two genuine findings: (1) residual abbreviation 'XY' at "
    "data/fixture.json line 120 (medium, complexity), and (2) duplicated word "
    "'the the' at line 340 (low, complexity). Output valid JSON only.{"
)


def _finding(**overrides: object) -> ReviewFinding:
    defaults: dict[str, object] = {
        "path": "a.py",
        "line": 1,
        "severity": Severity.low,
        "title": "Duplicated word 'the the' introduced by the rename",
        "body": "The mechanical rename left a doubled article.",
    }
    defaults.update(overrides)
    return ReviewFinding(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# the production shapes
# ---------------------------------------------------------------------------


def test_the_note_inside_a_body_is_self_talk() -> None:
    assert is_self_talk(_finding(body=_LEAKED_BODY))


def test_the_note_as_a_whole_finding_is_self_talk() -> None:
    assert is_self_talk(_finding(path="findings", title=_LEAKED_TITLE, body=""))


@pytest.mark.parametrize("field", ["failure_scenario", "suggestion"])
def test_every_model_authored_field_is_scanned(field: str) -> None:
    """A note can land in whichever string the model was writing when it stumbled."""
    assert is_self_talk(_finding(**{field: _LEAKED_BODY}))


# ---------------------------------------------------------------------------
# what must NOT be dropped: one half of the note alone is ordinary review prose
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        # Shape words alone — a genuine finding about a parser or an API.
        "The handler must return valid JSON even when `data` is None.",
        "`parse_findings` accepts a findings object with a trailing comma.",
        "Reject the reply when the body is not a findings array — no prose is allowed.",
        # Broken-attempt words alone — a genuine finding about error handling.
        "A corrupted response from upstream is retried without backoff.",
        "The retry discards the previous response before the new one arrives.",
        "Log a formatting error instead of raising when the date is malformed.",
        # `invalid JSON` is not `valid JSON`.
        "Returns invalid JSON when the list is empty, and the caller discards the output.",
    ],
)
def test_a_genuine_finding_carrying_one_half_of_the_note_is_kept(body: str) -> None:
    assert not is_self_talk(_finding(body=body))


def test_an_ordinary_finding_is_kept() -> None:
    assert not is_self_talk(_finding())


# ---------------------------------------------------------------------------
# the partition
# ---------------------------------------------------------------------------


def test_drop_self_talk_partitions_in_input_order() -> None:
    genuine = _finding(title="real bug")
    leaked = _finding(title=_LEAKED_TITLE, path="findings")
    other = _finding(title="another real bug", line=2)

    kept, dropped = drop_self_talk([genuine, leaked, other])

    assert kept == [genuine, other]
    assert dropped == [leaked]


def test_drop_self_talk_leaves_a_clean_list_alone() -> None:
    findings = [_finding(), _finding(line=2)]

    assert drop_self_talk(findings) == (findings, [])
