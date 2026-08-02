"""Output formatting for the local commands.

``render_findings`` turns engine findings into one of three text shapes:
``human`` (a readable listing), ``json`` (a machine-readable array), or
``agent`` (correction instructions an AI coding agent can read and apply).

``flatten_details`` adapts a body written for a GitHub comment — the change
diagram — to a terminal, which renders no HTML.
"""

from __future__ import annotations

import json
import re

from lgtmaybe.core.models import ReviewFinding

# The summary is written for a GitHub comment, so it can carry hidden markers
# (the incomplete-run flag). They mean nothing to a terminal — strip them rather
# than print raw HTML at the end of a local review.
_HIDDEN_MARKER_RE = re.compile(r"[ \t]*<!--.*?-->")

# The diagram body tucks each text rendering in a <details> block, which GitHub
# collapses and a terminal prints as raw tags. The summary is the block's label,
# so it becomes the section heading the tags were standing in for.
_DETAILS_OPEN_RE = re.compile(r"^\s*<details><summary>(.*?)</summary>\s*$")
_DETAILS_CLOSE_RE = re.compile(r"^\s*</details>\s*$")


def flatten_details(body: str) -> str:
    """Turn ``<details>`` blocks into labelled sections for a terminal.

    The Mermaid fences are left alone: a terminal can't draw them, but they are
    what you paste into a GitHub comment or mermaid.live, so dropping them would
    cost more than the noise it saved.
    """
    lines: list[str] = []
    for line in body.splitlines():
        opened = _DETAILS_OPEN_RE.match(line)
        if opened:
            lines.append(f"{opened.group(1)}:")
        elif not _DETAILS_CLOSE_RE.match(line):
            lines.append(line)
        elif lines and not lines[-1].strip():
            # The closing tag's leading blank line separated it from the fence
            # above; with the tag gone it would double up with the next one.
            lines.pop()
    return "\n".join(lines)


def render_findings(findings: list[ReviewFinding], summary: str, *, fmt: str = "human") -> str:
    """Format findings for the local CLI.

    ``fmt`` selects the output: ``human`` (a readable listing + summary),
    ``json`` (a machine-readable array), or ``agent`` (directive correction
    instructions for an AI coding agent to read and apply).
    """
    if fmt == "json":
        return json.dumps([f.model_dump(mode="json") for f in findings])
    summary = _HIDDEN_MARKER_RE.sub("", summary).strip()
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
            lines.extend(f"        {s}" for s in f.suggestion.splitlines())
        lines.append("")
    lines.append(
        f"{len(findings)} finding(s) to address. After applying the fixes, re-run "
        "`lgtmaybe review` to confirm they are resolved."
    )
    return "\n".join(lines)
