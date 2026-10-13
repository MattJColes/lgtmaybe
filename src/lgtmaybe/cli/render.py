"""Output formatting for the local commands.

``render_findings`` turns engine findings into one of four text shapes:
``human`` (a readable listing), ``json`` (a machine-readable array), ``agent``
(correction instructions an AI coding agent can read and apply), or ``sarif``
(a SARIF 2.1.0 log for code-scanning upload, see ``render_sarif``).

``flatten_details`` adapts a body written for a GitHub comment — the change
diagram — to a terminal, which renders no HTML.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from lgtmaybe.core.findings import finding_fingerprint, finding_identity
from lgtmaybe.core.models import ReviewFinding, Severity
from lgtmaybe.core.version import package_version
from lgtmaybe.engine import INCOMPLETE_MARKER

# How the agent format tells its reader to confirm the fixes, by default.
_CLI_RERUN = "`lgtmaybe review`"

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


def strip_hidden_markers(summary: str) -> str:
    """The summary without its hidden HTML markers, for anything that is not a PR."""
    return _HIDDEN_MARKER_RE.sub("", summary).strip()


def findings_payload(findings: list[ReviewFinding]) -> list[dict[str, Any]]:
    """The findings as JSON-ready dicts: the ``json`` format's one shape.

    Shared by ``--format json`` and the MCP server's structured result, so an
    agent reads the same fields whichever way it asked.
    """
    return [f.model_dump(mode="json") for f in findings]


def render_findings(
    findings: list[ReviewFinding],
    summary: str,
    *,
    fmt: str = "human",
    rerun_hint: str = _CLI_RERUN,
) -> str:
    """Format findings for the local CLI.

    ``fmt`` selects the output: ``human`` (a readable listing + summary),
    ``json`` (a machine-readable array), ``agent`` (directive correction
    instructions for an AI coding agent to read and apply), or ``sarif`` (a
    SARIF 2.1.0 log; the summary only decides whether the run was complete).
    ``rerun_hint`` is how the agent format tells its reader to confirm the
    fixes: the CLI command by default, the tool name when the reader came in
    over MCP.
    """
    if fmt == "json":
        return json.dumps(findings_payload(findings))
    if fmt == "sarif":
        return render_sarif(findings, complete=INCOMPLETE_MARKER not in summary)
    summary = strip_hidden_markers(summary)
    if fmt == "agent":
        return _render_agent(findings, summary, rerun_hint)

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


def _render_agent(findings: list[ReviewFinding], summary: str, rerun_hint: str) -> str:
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
        f"{rerun_hint} to confirm they are resolved."
    )
    return "\n".join(lines)


# SARIF 2.1.0 (OASIS). The schema URL is the canonical errata01 one; consumers
# (GitHub code scanning, GitLab, IDE viewers) key on `version`, not the URL.
SARIF_SCHEMA = (
    "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
)
_INFORMATION_URI = "https://lgtmaybe.coles.codes/"

# SARIF has three result levels (plus "none"), so the five-step Severity scale
# folds onto them. Keyed by every member and guarded by a test that compares the
# keys to the enum, so a new Severity cannot silently fall through.
SARIF_LEVELS: dict[Severity, str] = {
    Severity.critical: "error",
    Severity.high: "error",
    Severity.medium: "warning",
    Severity.low: "note",
    Severity.info: "note",
}

# The rule for a finding with no engine-stamped lens id (legacy inputs only).
_FALLBACK_RULE = "review"

# Versioned keys, as SARIF recommends for partialFingerprints: if either id's
# derivation ever changes, the key changes with it, so a consumer never matches
# an old hash against a new one. Both are the hidden ids the forge adapters embed
# in posted comments, and neither contains model prose.
_FINGERPRINT_KEY = "lgtmaybeFingerprint/v1"
_IDENTITY_KEY = "lgtmaybeIdentity/v1"


def render_sarif(findings: list[ReviewFinding], *, complete: bool = True) -> str:
    """Render findings as a SARIF 2.1.0 log with one run, deterministically.

    One rule per distinct lens id, sorted, so the rule table does not depend on
    finding order. A finding gets a ``region`` only when its line is real in the
    file the consumer has checked out: an unanchored finding's line is a guess,
    and a LEFT-side line numbers the old file. Either way the result still names
    the file. A suggestion is free text with no region it replaces, so it cannot
    be a SARIF ``fixes`` entry; it travels in the result's property bag instead.

    ``complete`` is False when not every lens call finished: the run's invocation
    then reports ``executionSuccessful: false`` with a warning notification, so a
    partial log is never mistaken for a clean bill of health.
    """
    rule_ids = sorted({f.category or _FALLBACK_RULE for f in findings})
    rule_index = {rule_id: i for i, rule_id in enumerate(rule_ids)}
    invocation: dict[str, object] = {"executionSuccessful": complete}
    if not complete:
        invocation["toolExecutionNotifications"] = [
            {
                "level": "warning",
                "message": {"text": "Not every review lens completed; results may be incomplete."},
            }
        ]
    log = {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "lgtmaybe",
                        "version": package_version(),
                        "informationUri": _INFORMATION_URI,
                        "rules": [_sarif_rule(rule_id) for rule_id in rule_ids],
                    }
                },
                "invocations": [invocation],
                "results": [_sarif_result(f, rule_index) for f in findings],
            }
        ],
    }
    # ASCII-escaped like the json format: a redirect on a non-UTF-8 console
    # (Windows' locale default) still writes a file every consumer decodes.
    return json.dumps(log, indent=2)


def _sarif_rule(rule_id: str) -> dict[str, object]:
    return {
        "id": rule_id,
        "shortDescription": {"text": f"lgtmaybe {rule_id} review lens"},
        "helpUri": _INFORMATION_URI,
    }


def _sarif_uri(path: str) -> str:
    """A repo-relative path as a relative URI reference: forward slashes, and
    every character a URI cannot carry literally (spaces, ``#``, ``?``, ``%``,
    non-ASCII) percent-encoded."""
    return quote(path.replace("\\", "/").lstrip("/"), safe="/")


def _sarif_result(finding: ReviewFinding, rule_index: dict[str, int]) -> dict[str, object]:
    rule_id = finding.category or _FALLBACK_RULE
    physical: dict[str, object] = {
        "artifactLocation": {"uri": _sarif_uri(finding.path), "uriBaseId": "%SRCROOT%"}
    }
    if finding.anchored and finding.side == "RIGHT":
        physical["region"] = {"startLine": finding.line}
    properties: dict[str, object] = {"severity": finding.severity.value}
    if finding.confidence is not None:
        properties["confidence"] = finding.confidence
    if finding.suggestion is not None:
        properties["suggestion"] = finding.suggestion
    if not finding.anchored:
        properties["anchored"] = False
    return {
        "ruleId": rule_id,
        "ruleIndex": rule_index[rule_id],
        "level": SARIF_LEVELS[finding.severity],
        "message": {"text": f"{finding.title}\n\n{finding.body}"},
        "locations": [{"physicalLocation": physical}],
        "partialFingerprints": {
            _FINGERPRINT_KEY: finding_fingerprint(finding.path, finding.title),
            _IDENTITY_KEY: finding_identity(finding),
        },
        "properties": properties,
    }
