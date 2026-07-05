"""Output formatting for the local ``review`` command.

Turns engine findings into one of three text shapes: ``human`` (a readable
listing), ``json`` (a machine-readable array), or ``agent`` (correction
instructions an AI coding agent can read and apply).
"""

from __future__ import annotations

import json

from lgtmaybe.core.models import ReviewFinding


def render_findings(findings: list[ReviewFinding], summary: str, *, fmt: str = "human") -> str:
    """Format findings for the local CLI.

    ``fmt`` selects the output: ``human`` (a readable listing + summary),
    ``json`` (a machine-readable array), or ``agent`` (directive correction
    instructions for an AI coding agent to read and apply).
    """
    if fmt == "json":
        return json.dumps([f.model_dump(mode="json") for f in findings])
    if fmt == "agent":
        return _render_agent(findings, summary)

    lines: list[str] = []
    for f in findings:
        score = f" (confidence {f.confidence}/10)" if f.confidence is not None else ""
        lines.append(f"{f.path}:{f.line}  [{f.severity.upper()}] {f.title}{score}")
        lines.append(f"  {f.body}")
        if f.suggestion is not None:
            lines.append(f"  suggestion: {f.suggestion}")
        lines.append("")
    lines.append(summary)
    return "\n".join(lines)


def _render_agent(findings: list[ReviewFinding], summary: str) -> str:
    """Render findings as correction instructions for an AI agent to apply."""
    if not findings:
        return f"No review findings — nothing to correct. {summary}"

    lines = [
        "Code review findings for your local changes. Act as the developer and "
        "apply each correction below: open the file at the given path and line, "
        "fix the issue, and apply the suggested change where one is given.",
        "",
    ]
    for i, f in enumerate(findings, 1):
        lines.append(f"[{i}] {f.path}:{f.line}  ({f.severity.upper()})  {f.title}")
        lines.append(f"    Issue: {f.body}")
        if f.suggestion is not None:
            lines.append("    Suggested fix:")
            lines.extend(f"        {s}" for s in f.suggestion.splitlines() or [f.suggestion])
        lines.append("")
    lines.append(
        f"{len(findings)} finding(s) to address. After applying the fixes, re-run "
        "`lgtmaybe review` to confirm they are resolved."
    )
    return "\n".join(lines)
