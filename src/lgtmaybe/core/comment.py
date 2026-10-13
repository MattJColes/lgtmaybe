"""Rendering the Markdown a review comment is made of.

Host-neutral, and in ``core`` for that reason: every forge lgtmaybe posts to
renders Markdown, so the severity badge, the demoted/broad body sections, the
fence defanging that stops attacker-controlled model prose escaping a code
block, and the hidden idempotency markers are the same on all of them. Only the
*transport* — which endpoint, which position vocabulary — belongs to an adapter.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TypeVar

from .findings import finding_fingerprint, finding_identity
from .models import ReviewFinding

_T = TypeVar("_T")

# The hidden per-finding ids stamped into every inline comment we post, and read
# back on a re-run to tell "already reported" from "new". Never search a comment
# body with these: read its ids through ``stamped_ids`` / ``finding_keys``.
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


# Any hidden lgtmaybe marker, of every family: the summary, describe and diagram
# upsert markers, the completion watermarks, the lenses/risk/incomplete gates
# and the per-finding ids. Model prose must never carry one (``strip_markers``).
_ANY_MARKER = re.compile(r"<!--\s*lgtmaybe.*?-->", re.DOTALL)


def strip_markers(text: str, pattern: re.Pattern[str] = _ANY_MARKER) -> str:
    """Remove every hidden lgtmaybe marker (or every ``pattern`` match) from text.

    Our markers are plain HTML comments, and on a fork PR the model writing a
    finding can be steered by the diff it reads. A marker in a finding lands in
    the posted body, where a reader would take it for one the adapter stamped:
    a completion watermark naming a commit the attacker pushes later (the next
    run skips it as reviewed), a lenses list a posting gate trusts, another
    setup's summary marker, or another finding's id. Repeated until nothing
    matches, so stripping an inner marker cannot splice an outer one together.
    """
    while (stripped := pattern.sub("", text)) != text:
        text = stripped
    return text


def model_prose(text: str) -> str:
    """Model prose made safe to render: fences defanged, hidden markers removed."""
    return defang_fences(strip_markers(text))


def last_match(pattern: re.Pattern[str], body: str | None) -> str | None:
    """The last capture of ``pattern`` in ``body``, or None.

    The last, because every adapter appends its own stamp after the prose it
    posts: an earlier match can only be one that rode in on that prose.
    """
    found: list[str] = pattern.findall(body or "")
    return found[-1] if found else None


def code_span(path: str, *, table: bool = False) -> str:
    """*path* as one inert inline code span.

    Filenames are attacker-chosen on a fork PR (git allows a newline in one) and
    some paths are model-written, so the raw value could end the span or the
    paragraph and render a live heading or link after it. Whitespace runs
    collapse to one space, backticks are stripped, and with *table* a ``|`` is
    escaped — inside a table row it ends the cell even within a code span.
    """
    text = " ".join(path.split()).replace("`", "")
    if table:
        text = text.replace("|", r"\|")
    return f"`{text}`"


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


# The change-overview comment's marker family, shared by every gateway's upsert
# and the existing-overview check so the two can never drift apart.
DIAGRAM_MARKER_FAMILY = "lgtmaybe-diagram"

# Completion watermarks driving incremental review, shared by every adapter that
# offers it so the wire format cannot drift between forges. The review summary
# carries the last head whose review fully posted; the diagram comment carries
# the last head whose required overview posted. The capture group is the SHA.
REVIEWED_MARKER = re.compile(r"<!-- lgtmaybe-reviewed:([0-9a-f]{7,40}) -->")
DIAGRAMMED_MARKER = re.compile(r"<!-- lgtmaybe-diagrammed:([0-9a-f]{7,40}) -->")

# A fenced Mermaid diagram — the one kind all three forges render. The info
# string is what marks it; the word in prose proves nothing. PlantUML is left
# out: GitHub and Gitea show it as source, which replaces no rendered overview.
_DIAGRAM_FENCE = re.compile(r"^[ \t>]*(?:`{3,}|~{3,})[ \t]*mermaid\b", re.IGNORECASE | re.MULTILINE)


def find_existing_overview(
    description: str, comments: Iterable[tuple[str, str]], *, own_marker: str
) -> str | None:
    """Where a diagram of this change already lives on the PR, or None.

    The author is named in inline code, never as an @-mention: the note rides
    every review summary, and a mention would notify them each time.

    Reads the PR description and each ``(author, body)`` conversation comment
    for a fenced diagram. Our own overview (``own_marker``, this provider/model's
    diagram marker) keeps the slot: once posted we keep refreshing it, since a
    frozen overview goes stale on the next push. Another lgtmaybe setup's
    overview counts as existing — it is the same duplicate from our side.
    """
    comments = list(comments)
    if any(own_marker in body for _author, body in comments):
        return None
    if _DIAGRAM_FENCE.search(description):
        return "the PR description"
    for author, body in comments:
        if _DIAGRAM_FENCE.search(body):
            return f"a comment by `{author}`" if author else "a comment"
    return None


def finding_bullet(f: ReviewFinding) -> str:
    """One finding as a Markdown list item — shared by both body sections."""
    return (
        f"- **[{f.severity.upper()}{finding_badge(f)}] {model_prose(f.title)}** "
        f"(`{f.path}`) — {model_prose(f.body)}"
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


def render_unread_files(paths: list[str]) -> str:
    """Name changed files the adapter could not read, as a summary body section.

    For hosts that serve no diff and fetch each file themselves: a file that
    never came back reaches the model as "changed, not shown", and the reader
    must be told the same rather than take the review as covering it. Paths
    are chosen by the PR author, so backticks are replaced to keep each one
    inside its code span. Returns "" when every file was read.
    """
    unique = list(dict.fromkeys(paths))
    if not unique:
        return ""
    lines = [
        "",
        "",
        "### Files not reviewed",
        "",
        "_These files changed but could not be read from the host, so they were not reviewed:_",
        "",
    ]
    lines += [f"- `{path.replace('`', chr(39))}`" for path in unique]
    return "\n".join(lines)


# The id block ``render_inline_body`` ends every finding comment with: the
# fingerprint, then the identity (absent on a body that predates it), then
# nothing but whitespace. Anchored to the end of the body because the text
# before it includes the suggestion, which is posted verbatim and so can carry
# any marker a steered model wrote into it. Only ``render_inline_body`` writes
# these bodies and no forge appends to them; a body someone later edits to
# carry text after the block stops reading as ours, which costs a re-post of
# its finding, never a skipped one.
_STAMPED_IDS = re.compile(rf"{FINDING_MARKER.pattern}(?:\s*{IDENTITY_MARKER.pattern})?\s*\Z")


def split_stamped_ids(body: str) -> tuple[str, str]:
    """Split a finding comment into everything before its id block, and the block.

    A body without a trailing id block comes back whole, with ``""`` as the
    block. Lets a caller rewrite the ids the adapter stamped (resolve-on-fix
    retiring them) without touching a look-alike inside the suggestion.
    """
    match = _STAMPED_IDS.search(body)
    return (body, "") if match is None else (body[: match.start()], body[match.start() :])


def stamped_ids(body: str | None) -> tuple[str | None, str | None]:
    """The ``(fingerprint, identity)`` the adapter stamped on a finding comment.

    Only the trailing id block counts. A suggestion is code applied with one
    click, so it is posted exactly as the model wrote it, and a model steered by
    a fork PR can write another finding's ids into it. Read as this comment's
    own, a forged id would make a later run skip that finding as already
    posted, resolve or keep open the wrong thread, or let a 👎 suppress it.
    """
    match = _STAMPED_IDS.search(body or "")
    return (None, None) if match is None else (match.group(1), match.group(2))


def finding_keys(body: str) -> set[str]:
    """Every active hidden id the adapter stamped on a comment (fingerprint + identity).

    Two ids of the same finding, pooled into one set so "have we posted this?" is
    a single intersection. A body predating the identity marker yields just its
    fingerprint, which still matches whenever the title is unchanged — so old
    conversations keep deduping exactly as they did. Ids anywhere else in the
    body are ignored (``stamped_ids``).
    """
    return {key for key in stamped_ids(body) if key}


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


def unposted(
    candidates: list[tuple[_T, ReviewFinding]], posted: list[set[str]]
) -> list[tuple[_T, ReviewFinding]]:
    """The candidates whose finding is not already among *posted*, in order.

    *posted* holds one id set per comment we already posted (see each adapter's
    ``_existing_finding_keys`` — which must count only comments the posting
    identity wrote, since anyone can type a hidden id). A candidate matches a
    comment sharing **either** id, so a reworded finding still dedupes on its
    prose-free identity. Matching is **one-for-one**: an identity is not unique
    within a file (two identical flagged lines share one), so each posted comment
    absorbs at most one candidate and an N+1th occurrence still posts.
    """
    unmatched = list(posted)
    fresh: list[tuple[_T, ReviewFinding]] = []
    for item, finding in candidates:
        keys = current_finding_keys([finding])
        already = next((i for i, existing in enumerate(unmatched) if existing & keys), None)
        if already is None:
            fresh.append((item, finding))
        else:
            unmatched.pop(already)  # consumed — it can't absorb a second candidate
    return fresh


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
        f"**[{f.severity.upper()}{finding_badge(f)}] {model_prose(f.title)}**"
        f"\n\n{model_prose(f.body)}"
    )
    if f.suggestion is not None:
        body += f"\n\n```suggestion\n{defang_fences(f.suggestion)}\n```"
    body += f"\n\n<!-- lgtmaybe-finding:{finding_fingerprint(f.path, f.title)} -->"
    body += f"\n<!-- lgtmaybe-identity:{finding_identity(f)} -->"
    return body
