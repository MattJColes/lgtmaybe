"""FakeEngine: a minimal ReviewEngine that exercises the ProviderClient port.

Demonstrates dependency injection — a provider is injected, the engine parses
its structured output into findings. The real engine is built in its track.
"""

from __future__ import annotations

import json

from lgtmaybe.core.models import PRContext, ReviewConfig, ReviewFinding
from lgtmaybe.core.ports import ProviderClient, ReviewEngine


class FakeEngine(ReviewEngine):
    def __init__(self, provider: ProviderClient) -> None:
        self._provider = provider
        self.fetch_file = None
        self.resolve_symbol = None

    def set_fetch_file(self, fetch_file) -> None:  # type: ignore[no-untyped-def]
        """Match LLMReviewEngine's setter so the CLI's local-review wiring works."""
        self.fetch_file = fetch_file

    def set_symbol_resolver(self, resolve_symbol) -> None:  # type: ignore[no-untyped-def]
        """Match LLMReviewEngine's setter so the CLI's symbol-resolution wiring works."""
        self.resolve_symbol = resolve_symbol

    def review(self, ctx: PRContext, cfg: ReviewConfig) -> tuple[list[ReviewFinding], str]:
        result = self._provider.complete([{"role": "user", "content": ctx.diff}], cfg.model)
        raw = json.loads(result.text)
        findings = [
            f
            for f in (ReviewFinding.model_validate(item) for item in raw)
            if f.severity >= cfg.min_severity
        ]
        summary = f"{len(findings)} findings · model {cfg.model}"
        return findings, summary
