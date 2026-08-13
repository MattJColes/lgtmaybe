"""Explicitly validate prior review findings against a newer PR head."""

from __future__ import annotations

import json
from typing import Any

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    ActiveFinding,
    FindingValidation,
    FindingValidationResult,
    FindingValidationStatus,
    PRContext,
    ReviewConfig,
)
from lgtmaybe.core.ports import ProviderClient

from .injection import wrap_validation
from .profiling import timed_complete
from .redact import redact

_log = get_logger(__name__)

_SYSTEM = """You are validating earlier pull-request review findings against a newer head.
For every supplied thread_id, return exactly one verdict:
- fixed: the current code clearly removes the reported failure;
- still_open: the reported failure clearly remains;
- uncertain: the supplied evidence cannot prove either result.
Never invent missing code or treat absence as proof. Return only the structured JSON object."""


def _uncertain(findings: list[ActiveFinding], reason: str) -> list[FindingValidation]:
    return [
        FindingValidation(
            thread_id=finding.thread_id,
            status=FindingValidationStatus.uncertain,
            reason=reason,
        )
        for finding in findings
    ]


def _context(findings: list[ActiveFinding], ctx: PRContext) -> str:
    prior = "\n\n".join(
        f"THREAD {finding.thread_id}\nPATH {finding.path}\n{finding.body}" for finding in findings
    )
    paths = {finding.path for finding in findings}
    current = "\n\n".join(
        f"--- {path} ---\n{text}" for path, text in ctx.file_contents.items() if path in paths
    )
    return redact(
        f"PRIOR FINDINGS\n{prior}\n\nCOMPARE DIFF\n{ctx.diff}\n\nCURRENT FILES\n{current}"
    )


def validate_findings(
    provider: ProviderClient,
    cfg: ReviewConfig,
    findings: list[ActiveFinding],
    ctx: PRContext,
) -> list[FindingValidation]:
    """Return one safe verdict per active finding; ambiguity is uncertain."""
    if not findings:
        return []
    context = _context(findings, ctx)
    if len(context) > cfg.max_input_tokens * 4:
        return _uncertain(findings, "validation context exceeds the input budget")
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": wrap_validation(context)},
    ]
    opts: dict[str, Any] = (
        {"response_format": FindingValidationResult} if cfg.structured_output else {}
    )
    try:
        result = timed_complete(
            provider,
            messages,
            model=cfg.reflect_model or cfg.model,
            label="validate",
            **opts,
        )
        parsed = FindingValidationResult.model_validate(json.loads(result.text))
    except Exception as exc:  # noqa: BLE001 - validation fails closed
        _log.warning("follow-up finding validation failed: %s", exc)
        return _uncertain(findings, "validation output was unavailable or invalid")

    expected = {finding.thread_id for finding in findings}
    grouped: dict[str, list[FindingValidation]] = {thread_id: [] for thread_id in expected}
    for verdict in parsed.verdicts:
        if verdict.thread_id in grouped:
            grouped[verdict.thread_id].append(verdict)
    output: list[FindingValidation] = []
    for finding in findings:
        verdicts = grouped[finding.thread_id]
        if len(verdicts) == 1:
            output.append(verdicts[0])
        else:
            output.extend(_uncertain([finding], "validation verdict was missing or duplicated"))
    return output
