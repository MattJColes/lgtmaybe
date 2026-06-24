"""Self-reflection pass: ask the provider to judge confidence in each finding.

Drops findings the model marks as low-confidence (keep=False). The verdict is
constrained to a structured schema (litellm ``response_format``) the same way the
review calls are, with a lenient parser + keep-all safe default as fallback.
"""

from __future__ import annotations

import json
from typing import Any

from lgtmaybe.core.models import PRContext, ReflectionResult, ReviewConfig, ReviewFinding
from lgtmaybe.core.ports import ProviderClient

from .compress import count_tokens
from .parse import iter_json_values
from .redact import redact

# Floor on the grounding budget: when the diff + findings already fill (or
# overflow) the input budget there's no room for file text, so we attach none —
# today's behavior. A small positive floor lets a sliver of head text through
# rather than nothing when the budget is merely tight.
_MIN_GROUNDING_TOKENS = 256

_REFLECT_SYSTEM = """\
You are a senior code reviewer auditing another reviewer's findings for false positives.

Given a list of findings (as JSON) and the diff that generated them, return a JSON object \
with a single key "verdicts": a list of \
{"index": <finding index>, "keep": <true|false>, "broad": <true|false>} objects, \
one per finding.

For each KEPT finding also classify its actionability with "broad": set it true when fixing \
the finding needs a BROAD change — a redesign, an infrastructure/config change, an \
API/contract change, or one whose correctness needs verification you cannot do from the diff \
— and false for a safe, self-contained edit a developer can apply on the spot. A dropped \
finding's "broad" is ignored, so emit false there.

Keep a finding only if you are confident it is a real issue in the actual changed code.
Drop it if it is speculative, out of scope, or referring to unchanged lines.

Also drop a finding that merely DESCRIBES the change without naming a concrete problem \
("X was removed", "Y now takes a new parameter", "this method is now async") — narration \
that restates the diff is not a finding, only a changelog. Be especially strict with \
low-severity (info/low) findings: keep one only when it names a specific, actionable issue, \
not just an observation about what changed.

Also drop a finding whose validity depends on an assumption about code NOT shown in the \
diff. The diff is only a slice of the codebase: base classes, guards, validators, \
idempotency checks, callers, and config may live in files you cannot see. A finding that \
asserts something is "missing", "unguarded", "never handled", or "will break" — when that \
handling could plausibly exist elsewhere — is a likely false positive; drop it unless the \
diff itself shows the absence. A finding that already hedges this ("if there is no X \
elsewhere…") may be kept at low severity.

Gap findings are valid types, not false positives: a missing test, a missing or stale \
docstring, a performance or complexity concern, or a mismatch with the PR's stated intent. \
For those, judge whether the gap or mismatch is real — not whether the changed line \
itself is buggy.

Also apply these three drop-rules:
(a) Drop a finding that asserts library/cloud/framework SEMANTICS the diff does not itself \
prove — "this SDK call swallows errors", "this API is rate-limited", "this ORM method runs \
N+1 queries", "this decorator changes the return type". Unless the diff (or the file text \
below) shows the behaviour, that is a guess about third-party internals, not a finding.
(b) The full head text of each flagged file is provided below for exactly this check: drop a \
"missing import", "missing await", "undefined symbol", or "duplicated across hunks" claim \
when the provided file text shows the import / symbol / await IS present (the diff is only a \
slice, so a symbol a finding calls undefined often appears elsewhere in the same file).
(c) Drop a finding claiming "this existing test will fail", "this needs a mock", or "the \
patch target is wrong" — test-execution and mock/patch-target outcomes depend on the full \
test harness you cannot see, so such a claim is speculative.

Return ONLY the JSON object, nothing else. Example:
{"verdicts": [{"index": 0, "keep": true, "broad": false}, \
{"index": 1, "keep": false, "broad": false}]}
"""


def reflect_findings(
    findings: list[ReviewFinding],
    ctx: PRContext,
    cfg: ReviewConfig,
    provider: ProviderClient,
) -> list[ReviewFinding]:
    """Filter *findings* by asking the provider to score confidence.

    Returns only findings the provider marks as keep=True. If the verdict can't be
    parsed, keeps everything (safe default — better an unfiltered finding than a
    dropped real one).
    """
    if not findings:
        return []

    findings_json = json.dumps([f.model_dump(mode="json") for f in findings], indent=2)

    # Asymmetric grounding: the reviews ran per-batch on slices; here the auditor
    # gets the full (redacted) head text of every file carrying a surviving
    # finding so it can verify a whole-file claim — that an import/symbol IS
    # present, that a duplicate isn't real — instead of guessing about unseen code.
    reserve = cfg.max_input_tokens - count_tokens(ctx.diff) - count_tokens(findings_json)
    grounding = _grounding_block(findings, ctx, reserve)

    user_content = (
        f"Diff:\n{ctx.diff}\n\n"
        f"{grounding}"
        f"Findings (indexed from 0):\n{findings_json}\n\n"
        "Return the confidence verdict JSON object."
    )

    opts: dict[str, Any] = {"response_format": ReflectionResult} if cfg.structured_output else {}
    result = provider.complete(
        messages=[
            {"role": "system", "content": _REFLECT_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        model=cfg.reflect_model or cfg.model,
        **opts,
    )

    try:
        verdicts = _parse_verdicts(result.text)
    except Exception:
        # If reflection fails to parse, keep all findings (safe default), each
        # non-broad — never silently drop a real finding, nor tier it as broad.
        return findings

    survivors: list[ReviewFinding] = []
    for i, finding in enumerate(findings):
        keep, broad = verdicts.get(i, (True, False))
        if keep:
            survivors.append(finding.model_copy(update={"broad": broad}))
    return survivors


def _grounding_block(
    findings: list[ReviewFinding], ctx: PRContext, budget_tokens: int
) -> str:
    """Redacted head text of the files carrying a finding, fit into *budget_tokens*.

    Only files in ``{f.path for f in findings}`` are included, walked
    most-flagged-first so the file the auditor most needs lands first. Each file's
    text is redacted (``file_contents`` is RAW head text — this is the one leak
    path to get right) and head+tail-truncated if a single file would exceed the
    remaining budget. Returns "" (today's behavior: diff-only) when the budget is
    non-positive or no flagged file has fetched head text.
    """
    if budget_tokens < _MIN_GROUNDING_TOKENS or not ctx.file_contents:
        return ""

    counts: dict[str, int] = {}
    for f in findings:
        counts[f.path] = counts.get(f.path, 0) + 1
    # Most-flagged first; stable on ties by first appearance order of the path.
    order = sorted(counts, key=lambda p: counts[p], reverse=True)

    remaining = budget_tokens
    blocks: list[str] = []
    for path in order:
        raw = ctx.file_contents.get(path)
        if not raw:
            continue
        text = redact(raw)
        if count_tokens(text) > remaining:
            text = _head_tail(text, remaining)
        used = count_tokens(text)
        if used <= 0:
            continue
        blocks.append(f"--- {path} ---\n{text}")
        remaining -= used
        if remaining < _MIN_GROUNDING_TOKENS:
            break

    if not blocks:
        return ""
    body = "\n\n".join(blocks)
    return (
        "Full head text of the changed files (for verification only):\n"
        f"{body}\n\n"
    )


def _head_tail(text: str, max_tokens: int) -> str:
    """Keep the head and tail of *text* so its token count fits within *max_tokens*.

    Whole-file claims hinge on imports (top of file) and the symbol/usage near the
    flagged code, so keeping both ends — with a marker where the middle was cut —
    is more useful for verification than a head-only truncation.
    """
    if max_tokens <= 0:
        return ""
    lines = text.split("\n")
    if count_tokens(text) <= max_tokens:
        return text

    marker = "… [truncated] …"
    half = max(1, (max_tokens - count_tokens(marker)) // 2)

    head: list[str] = []
    used = 0
    for line in lines:
        t = count_tokens(line) + 1
        if used + t > half:
            break
        head.append(line)
        used += t

    tail: list[str] = []
    used = 0
    for line in reversed(lines):
        t = count_tokens(line) + 1
        if used + t > half:
            break
        tail.append(line)
        used += t
    tail.reverse()

    return "\n".join([*head, marker, *tail])


def _parse_verdicts(raw: str) -> dict[int, tuple[bool, bool]]:
    """Parse the reflection verdict into an ``{index: (keep, broad)}`` map.

    Accepts the structured ``{"verdicts": [{"index": i, "keep": bool, "broad":
    bool}, ...]}`` envelope (``broad`` optional, default False), and (as a fallback
    for models that ignore the schema) the legacy ``{"0": true, "1": false}``
    index-to-bool map (no actionability tier, so broad defaults False). Shares the
    findings parser's lenient extraction (:func:`iter_json_values`), so reasoning
    blocks, code fences, and surrounding prose — including the bracket-bearing
    prose that an ``openai-compatible`` gateway without JSON mode emits — are
    tolerated.
    """
    for data in iter_json_values(raw):
        if not isinstance(data, dict):
            continue
        if isinstance(data.get("verdicts"), list):
            out: dict[int, tuple[bool, bool]] = {}
            for v in data["verdicts"]:
                if isinstance(v, dict) and "index" in v and "keep" in v:
                    out[int(v["index"])] = (bool(v["keep"]), bool(v.get("broad", False)))
            return out
        # legacy {"0": true, ...} — a dict of digit keys to bools (no broad tier).
        if data and all(str(k).lstrip("-").isdigit() for k in data):
            return {int(k): (bool(val), False) for k, val in data.items()}

    raise ValueError("unrecognised or unparseable verdict shape")
