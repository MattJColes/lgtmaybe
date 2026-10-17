"""Structured-output schemas for the auxiliary model passes.

Each class is the fixed-shape envelope one non-review model call must answer
in: describe, diagram, high impact, triage and slash-command answers. They are
enforced as a JSON schema through litellm ``response_format`` and validated
before anything renders. They live apart from ``core.models`` because they
describe what a model returns, not how a review runs or what a forge sends.

Kept in ``core`` rather than beside each feature so the engine (triage) and the
CLI (answers) import them without reaching into ``features``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    # Drifted or injected fields fail validation, as in core.models.
    model_config = ConfigDict(extra="forbid")


class FileWalkthrough(_Strict):
    """One entry of a PR description's per-file walkthrough."""

    path: str
    summary: str = ""


class DescribeResult(_Strict):
    """Structured-output envelope for the describe pass.

    A fixed-shape object so it can be enforced as a JSON schema via litellm
    ``response_format``. Every field beyond the title is optional with an
    empty default, so a model that answers partially still validates; a fully
    unparseable answer falls back to the raw text.
    """

    title: str
    change_type: str = ""
    summary: str = ""
    walkthrough: list[FileWalkthrough] = Field(default_factory=list)
    intent_check: str = ""


class DiagramNode(_Strict):
    """One component in the model's presentation-agnostic change graph."""

    id: str
    label: str
    technology: str = ""
    description: str = ""
    change: Literal["unchanged", "changed", "new"] = "unchanged"


class DiagramEdge(_Strict):
    """One directed relationship between two diagram node ids."""

    source: str
    target: str
    label: str = ""


class DiagramStep(_Strict):
    """One ordered run-time interaction between two diagram node ids.

    Where an edge says two components are related, a step says what happens and
    when — the behaviour view of the same change. ``reply`` renders the dashed
    return arrow.
    """

    source: str
    target: str
    label: str = ""
    reply: bool = False


class DiagramResult(_Strict):
    """Structured-output envelope for the change-diagram pass.

    The model returns presentation-agnostic nodes, edges, and ordered steps.
    lgtmaybe renders Mermaid (a flowchart of the structure, a sequence diagram
    of the flow) and text locally so model-authored syntax never reaches a
    Mermaid fence.
    """

    title: str = ""
    summary: str = ""
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)
    steps: list[DiagramStep] = Field(default_factory=list)
    notes: str = ""


#: The closed taxonomy of the change overview's High Impact Areas section: the
#: kinds of change a reviewer must not miss. Ordered as they render, worst-blast-
#: radius first. A closed Literal on purpose — an invented area would render an
#: unknown heading, so drifted output is rejected at the boundary instead.
HighImpactKind = Literal[
    "infrastructure",
    "security",
    "availability",
    "data_migration",
    "backup_and_recovery",
    "compatibility",
    "observability",
    "dependencies",
    "cost",
    "compliance",
]


class HighImpactArea(_Strict):
    """One call-out in the High Impact Areas section.

    ``files`` are paths from the diff (any path the PR did not change is
    dropped before rendering), ``why`` is the blast radius and ``check`` the
    one thing a reviewer should verify.
    """

    area: HighImpactKind
    title: str
    files: list[str] = Field(default_factory=list)
    why: str = ""
    check: str = ""
    severity: Literal["medium", "high", "critical"] = "high"


class HighImpactResult(_Strict):
    """Structured-output envelope for the high-impact pass.

    An empty ``areas`` list is a real answer — "nothing here qualifies" — and
    renders as the checked-and-clear line, never as a missing section.
    """

    areas: list[HighImpactArea] = Field(default_factory=list)
    notes: str = ""


class AnswerResult(_Strict):
    """Task-specific structured envelope for a slash-command answer."""

    answer: str


class TriageFileVerdict(_Strict):
    """One triage verdict: whether *path* needs the strong model, and how risky."""

    path: str
    review: bool = True
    risk: int = Field(default=5, ge=0, le=10)


class TriageResult(_Strict):
    """Structured-output envelope for the triage pass: ``{"files": [...]}``.

    A fixed-shape object so it can be enforced as a JSON schema via litellm
    ``response_format``, the same way reviews and reflection verdicts are.
    """

    files: list[TriageFileVerdict]
