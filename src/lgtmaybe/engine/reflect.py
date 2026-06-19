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

from .parse import iter_json_values

_REFLECT_SYSTEM = """\
You are a senior code reviewer auditing another reviewer's findings for false positives.

Given a list of findings (as JSON) and the diff that generated them, return a JSON object \
with a single key "verdicts": a list of {"index": <finding index>, "keep": <true|false>} objects, \
one per finding.

Keep a finding only if you are confident it is a real issue in the actual changed code.
Drop it if it is speculative, out of scope, or referring to unchanged lines.

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

Return ONLY the JSON object, nothing else. Example:
{"verdicts": [{"index": 0, "keep": true}, {"index": 1, "keep": false}]}
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
    user_content = (
        f"Diff:\n{ctx.diff}\n\n"
        f"Findings (indexed from 0):\n{findings_json}\n\n"
        "Return the confidence verdict JSON object."
    )

    opts: dict[str, Any] = {"response_format": ReflectionResult} if cfg.structured_output else {}
    result = provider.complete(
        messages=[
            {"role": "system", "content": _REFLECT_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        model=cfg.model,
        **opts,
    )

    try:
        verdicts = _parse_verdicts(result.text)
    except Exception:
        # If reflection fails to parse, keep all findings (safe default).
        return findings

    return [finding for i, finding in enumerate(findings) if verdicts.get(i, True)]


def _parse_verdicts(raw: str) -> dict[int, bool]:
    """Parse the reflection verdict into an ``{index: keep}`` map.

    Accepts the structured ``{"verdicts": [{"index": i, "keep": bool}, ...]}``
    envelope, and (as a fallback for models that ignore the schema) the legacy
    ``{"0": true, "1": false}`` index-to-bool map. Shares the findings parser's
    lenient extraction (:func:`iter_json_values`), so reasoning blocks, code
    fences, and surrounding prose — including the bracket-bearing prose that an
    ``openai-compatible`` gateway without JSON mode emits — are tolerated.
    """
    for data in iter_json_values(raw):
        if not isinstance(data, dict):
            continue
        if isinstance(data.get("verdicts"), list):
            out: dict[int, bool] = {}
            for v in data["verdicts"]:
                if isinstance(v, dict) and "index" in v and "keep" in v:
                    out[int(v["index"])] = bool(v["keep"])
            return out
        # legacy {"0": true, ...} — a dict of digit keys to bools.
        if data and all(str(k).lstrip("-").isdigit() for k in data):
            return {int(k): bool(val) for k, val in data.items()}

    raise ValueError("unrecognised or unparseable verdict shape")
