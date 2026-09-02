"""FakeProvider: returns canned findings, records every call."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from lgtmaybe.core.models import ProviderResult, ReviewFinding, Severity
from lgtmaybe.core.ports import Message

_DEFAULT_FINDINGS = [
    ReviewFinding(
        path="a.py",
        line=1,
        severity=Severity.low,
        title="canned finding",
        body="from FakeProvider",
        failure_scenario="When the changed path runs, it produces the reported failure.",
    )
]


class FakeProvider:
    """A ProviderClient that returns canned findings as JSON text."""

    def __init__(
        self,
        findings: list[ReviewFinding] | None = None,
        result: ProviderResult | None = None,
        results_by_schema: Mapping[type[BaseModel], ProviderResult] | None = None,
    ) -> None:
        self._findings = _DEFAULT_FINDINGS if findings is None else findings
        self._result = result
        self._results_by_schema = results_by_schema or {}
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        if self._result is not None:
            return self._result
        # Callers that make several differently-shaped structured calls (the
        # change overview makes three concurrently) route on the schema they
        # asked for; anything unrouted — including a call with no
        # response_format at all — still gets the canned findings.
        routed = self._results_by_schema.get(opts.get("response_format"))
        if routed is not None:
            return routed
        text = json.dumps([f.model_dump(mode="json") for f in self._findings])
        return ProviderResult(text=text, input_tokens=10, output_tokens=20)
