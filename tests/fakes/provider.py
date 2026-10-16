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


class RecoveringFakeProvider(FakeProvider):
    """A FakeProvider that also offers ``SupportsRecovery``, every step declined.

    The engine probes the recovery steps as one protocol, so a fake that wants
    to take one step (a step-down, an escalation) inherits the neutral answer
    for the rest: no budget opinion, the schema still sent, nothing remembered,
    no lower effort, no off switch, no second model. Each neutral answer is the
    one the engine treats exactly like a provider that offers no recovery.
    """

    model: str = ""

    def input_budget(self) -> int | None:
        return None

    def sends_response_format(self, model: str) -> bool:
        return True

    def drop_response_format(self, model: str, why: str) -> None:
        return None

    def lower_reasoning_effort(self) -> dict[str, Any] | None:
        return None

    def disable_reasoning(self) -> dict[str, Any] | None:
        return None

    def escalate_model(self) -> str | None:
        return None
