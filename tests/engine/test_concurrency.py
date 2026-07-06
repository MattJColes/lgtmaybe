"""Tests for the flattened review fan-out and its concurrency resolution."""

from __future__ import annotations

import threading
import time

import pytest

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
)
from lgtmaybe.engine import LLMReviewEngine
from lgtmaybe.engine.compress import count_tokens
from lgtmaybe.engine.engine import _resolve_workers
from tests.fakes import FakeProvider

_CLOUD_PROVIDERS = [
    p for p in Provider if p not in (Provider.ollama, Provider.openai_compatible)
]


def _cfg(provider: Provider, **overrides: object) -> ReviewConfig:
    return ReviewConfig(provider=provider, model="m", **overrides)


class TestResolveWorkers:
    @pytest.mark.parametrize("provider", _CLOUD_PROVIDERS)
    def test_cloud_defaults_to_eight(self, provider: Provider) -> None:
        assert _resolve_workers(_cfg(provider), task_count=100) == 8

    @pytest.mark.parametrize(
        "provider", [Provider.ollama, Provider.openai_compatible]
    )
    def test_single_stream_providers_default_to_one(self, provider: Provider) -> None:
        """ollama serves serially; openai-compatible may front a single-slot
        llama.cpp server, so the honest default is 1 (vLLM users raise it)."""
        assert _resolve_workers(_cfg(provider), task_count=100) == 1

    @pytest.mark.parametrize("provider", list(Provider))
    def test_explicit_max_concurrency_wins_everywhere(self, provider: Provider) -> None:
        assert _resolve_workers(_cfg(provider, max_concurrency=3), task_count=100) == 3

    def test_never_more_workers_than_tasks(self) -> None:
        assert _resolve_workers(_cfg(Provider.openai), task_count=2) == 2
        assert _resolve_workers(_cfg(Provider.openai, max_concurrency=16), task_count=5) == 5

    def test_at_least_one_worker(self) -> None:
        assert _resolve_workers(_cfg(Provider.openai), task_count=0) == 1


class _ConcurrencyTrackingProvider(FakeProvider):
    """Counts in-flight completions so tests can assert the pool's real width."""

    def __init__(self, delay: float = 0.05) -> None:
        super().__init__()
        self._delay = delay
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_in_flight = 0

    def complete(self, messages, model, **opts):  # type: ignore[override]
        with self._lock:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            time.sleep(self._delay)
            return ProviderResult(text='{"findings": []}', input_tokens=1, output_tokens=1)
        finally:
            with self._lock:
                self._in_flight -= 1


def _multi_file_ctx(n_files: int, lines_per_file: int = 40) -> PRContext:
    parts, files = [], []
    for i in range(n_files):
        path = f"f{i}.py"
        body = "".join(f"+content_{i}_{j}\n" for j in range(lines_per_file))
        header = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        parts.append(f"{header}@@ -1,1 +1,{lines_per_file} @@\n{body}")
        files.append(path)
    return PRContext(
        diff="".join(parts),
        changed_files=files,
        base_sha="a",
        head_sha="b",
        repo="org/repo",
        pr_number=1,
    )


class TestFlattenedFanOut:
    def test_calls_from_different_batches_run_concurrently(self) -> None:
        """The old per-batch pool serialised batches; the flat pool must not.

        Two batches × one lens with a 2-wide pool: both calls must be in
        flight together, which the per-batch shape could never produce.
        """
        ctx = _multi_file_ctx(2)
        one_file_tokens = count_tokens(ctx.diff) // 2 + 20  # each batch = 1 file
        cfg = ReviewConfig(
            provider=Provider.openai,
            model="m",
            categories=[ReviewCategory.security],
            max_input_tokens=one_file_tokens,
            max_concurrency=2,
            reflect=False,
            recursive=False,
        )
        provider = _ConcurrencyTrackingProvider()
        LLMReviewEngine(provider).review(ctx, cfg)
        assert provider.max_in_flight == 2

    def test_max_concurrency_bounds_the_pool(self) -> None:
        ctx = _multi_file_ctx(1)
        cfg = ReviewConfig(
            provider=Provider.openai,
            model="m",
            max_concurrency=2,
            reflect=False,
        )
        provider = _ConcurrencyTrackingProvider()
        LLMReviewEngine(provider).review(ctx, cfg)
        assert provider.max_in_flight <= 2

    def test_one_failing_lens_does_not_abort_the_others(self) -> None:
        calls: list[str] = []
        lock = threading.Lock()

        class _OneBadLens(FakeProvider):
            def complete(self, messages, model, **opts):  # type: ignore[override]
                prompt = "\n".join(str(m.get("content", "")) for m in messages)
                with lock:
                    calls.append(prompt[:20])
                if "Security review" in prompt:
                    raise RuntimeError("boom")
                return ProviderResult(text='{"findings": []}', input_tokens=1, output_tokens=1)

        cfg = ReviewConfig(
            provider=Provider.openai,
            model="m",
            categories=[ReviewCategory.security, ReviewCategory.performance],
            reflect=False,
        )
        findings, summary = LLMReviewEngine(_OneBadLens()).review(_multi_file_ctx(1), cfg)
        assert len(calls) == 2
        assert "1 of 2 review calls failed" in summary
