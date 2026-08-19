"""Rendering the Markdown a review comment is made of.

Host-neutral, and in ``core`` for that reason: every forge lgtmaybe posts to
renders Markdown, so the severity badge, the demoted/broad body sections, the
fence defanging that stops attacker-controlled model prose escaping a code
block, and the hidden idempotency markers are the same on all of them. Only the
*transport* — which endpoint, which position vocabulary — belongs to an adapter.
"""

from __future__ import annotations

import re

from .findings import finding_fingerprint, finding_identity
from .models import ReviewFinding

# The hidden per-finding ids stamped into every inline comment we post, and read
# back on a re-run to tell "already reported" from "new".
FINDING_MARKER = re.compile(r"<!-- lgtmaybe-finding:([0-9a-f]+) -->")
IDENTITY_MARKER = re.compile(r"<!-- lgtmaybe-identity:([0-9a-f]+) -->")

# Zero-width space, inserted to break up a triple-backtick run so it can't be
# parsed as a Markdown fence delimiter.
_ZWSP = "​"


def defang_fences(text: str) -> str:
    """Neutralise embedded triple-backticks in model-supplied text (title, body,
    suggestion) so it can't break out of a Markdown fence and inject content
    (e.g. a phishing link) into the rendered comment.

    The diff is attacker-controlled on a fork PR, so a prompt injection that
    survives the guard could steer the model into fence-breaking output. We insert
    zero-width spaces between the backticks: the run no longer reads as a fence,
    while the text stays visually intact.
    """
    return text.replace("```", f"`{_ZWSP}`{_ZWSP}`")


def finding_badge(f: ReviewFinding) -> str:
    """The provenance suffix for a finding's title line: lens, then confidence.

    Both values are already computed and already shown by the local CLI — the
    lens the engine stamped (``category``) and the 0-10 score the reflection
    auditor gave it (``confidence``) — but a GitHub reader could never see them.
    They answer the two questions a reviewer asks of a bot comment: which pass
    raised this, and how sure was it. Rendered inside the existing severity
    brackets (``**[HIGH · security · 80%] Title**``) so the title line gains no
    new visual furniture, and appended by the caller so it can never displace the
    hidden fingerprint/identity markers that key re-run dedupe.

    The 0-10 score is shown as a **percentage** — "how likely is this a real
    issue" reads plainly as ``80%``, where ``8/10`` invites a reader to mistake
    it for a rating out of ten. The scale is unchanged; only the rendering is.

    Each half is omitted when absent, so nothing renders empty: no category (a
    legacy finding) means no badge at all — byte-identical to the pre-badge
    rendering — and no score (``--no-reflect``, or a deterministic
    static-analysis finding) means just the lens. A ``0`` is a real verdict, not
    a missing one, so it renders.
    """
    if not f.category:
        return ""
    badge = f" · {defang_fences(f.category)}"
    return badge if f.confidence is None else f"{badge} · {f.confidence * 10}%"


def marker(family: str, key: str | None) -> str:
    """A hidden idempotency marker for one comment *family*.

    Scoped to *key* (a provider/model) when there is one, so concurrent reviews
    from different backends update their own comment instead of clobbering each
    other; an unkeyed gateway keeps the legacy unscoped marker (``_MARKER`` and
    its describe/diagram siblings).
    """
    return f"<!-- {family}:{key} -->" if key else f"<!-- {family} -->"


def finding_bullet(f: ReviewFinding) -> str:
    """One finding as a Markdown list item — shared by both body sections."""
    return (
        f"- **[{f.severity.upper()}{finding_badge(f)}] {defang_fences(f.title)}** "
        f"(`{f.path}`) — {defang_fences(f.body)}"
    )


def render_demoted(demoted: list[ReviewFinding]) -> str:
    """Render findings that couldn't be confidently placed inline as a body section.

    These keep their severity, file, and explanation — only the precise line (and
    its one-click suggestion) is dropped, because we could not anchor it. Returns
    "" when there is nothing to demote, so a normal review's body is unchanged.
    """
    if not demoted:
        return ""
    lines = [
        "",
        "",
        "### Additional findings",
        "",
        "_These relate to the changes but aren't tied to a single line:_",
        "",
    ]
    lines += [finding_bullet(f) for f in demoted]
    return "\n".join(lines)


def render_broad(broad: list[ReviewFinding]) -> str:
    """Render broad (redesign / infra / contract / needs-verification) findings.

    These are real findings the reflection pass judged too wide-reaching to action
    on a single line, so they're collapsed into a ``<details>`` block to keep the
    must-fix inline list tight without dropping the observation. Returns "" when
    there is nothing broad, so a normal review's body is unchanged.
    """
    if not broad:
        return ""
    lines = [
        "",
        "",
        "<details><summary>Broader observations</summary>",
        "",
        "_These are wider-reaching — a redesign, an infra/contract change, or one "
        "needing independent verification — so they're collected here rather than "
        "pinned to a line:_",
        "",
    ]
    lines += [finding_bullet(f) for f in broad]
    lines += ["", "</details>"]
    return "\n".join(lines)


def finding_keys(body: str) -> set[str]:
    """Every active hidden id carried by a comment body (fingerprint + identity).

    Two ids of the same finding, pooled into one set so "have we posted this?" is
    a single intersection. A body predating the identity marker yields just its
    fingerprint, which still matches whenever the title is unchanged — so old
    conversations keep deduping exactly as they did.
    """
    return set(FINDING_MARKER.findall(body)) | set(IDENTITY_MARKER.findall(body))


def current_finding_keys(findings: list[ReviewFinding]) -> set[str]:
    """Both hidden ids for every finding this run produced.

    The counterpart to ``_finding_keys``: what an existing conversation is matched
    against to decide whether its finding is still being reported.
    """
    keys: set[str] = set()
    for f in findings:
        keys.add(finding_fingerprint(f.path, f.title))
        keys.add(finding_identity(f))
    return keys


def render_inline_body(f: ReviewFinding) -> str:
    """The full body of one inline finding comment, hidden ids included.

    Shared by every adapter: the text of a finding does not change with the host,
    only the position fields wrapped around it. Ends with the two hidden ids so a
    later run can recognise this conversation — to skip re-posting the finding,
    and to auto-resolve the thread once it is gone. The fingerprint keys the
    user-facing channels (``ignore_fingerprints``, 👎 feedback) and hashes the
    title; the identity is prose-free, so it still matches after the model
    rewords the same finding. Either one matching means "already posted".
    """
    body = (
        f"**[{f.severity.upper()}{finding_badge(f)}] {defang_fences(f.title)}**"
        f"\n\n{defang_fences(f.body)}"
    )
    if f.suggestion is not None:
        body += f"\n\n```suggestion\n{defang_fences(f.suggestion)}\n```"
    body += f"\n\n<!-- lgtmaybe-finding:{finding_fingerprint(f.path, f.title)} -->"
    body += f"\n<!-- lgtmaybe-identity:{finding_identity(f)} -->"
    return body
