"""Tests for the pipeline timing instrumentation (engine/profiling.py)."""

from __future__ import annotations

import json

import pytest

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
)
from lgtmaybe.engine import LLMReviewEngine
from lgtmaybe.engine.profiling import Profiler, profiler, timed_complete
from tests.fakes import FakeProvider

_CTX = PRContext(
    diff="@@ -1,3 +1,4 @@\n context\n+new line\n context\n",
    changed_files=["a.py"],
    base_sha="abc",
    head_sha="def",
    repo="org/repo",
    pr_number=1,
)


@pytest.fixture(autouse=True)
def _fresh_profiler():
    """Each test starts from an empty module-level profiler."""
    profiler.reset()
    yield
    profiler.reset()


class TestProfiler:
    def test_stage_records_elapsed_time(self) -> None:
        p = Profiler()
        with p.stage("redact"):
            pass
        assert [s.name for s in p.stages] == ["redact"]
        assert p.stages[0].elapsed >= 0

    def test_stage_records_even_when_the_stage_raises(self) -> None:
        p = Profiler()
        with pytest.raises(ValueError):
            with p.stage("boom"):
                raise ValueError("stage failed")
        assert [s.name for s in p.stages] == ["boom"]

    def test_record_call_captures_usage_and_cache_counters(self) -> None:
        p = Profiler()
        p.record_call(
            label="security",
            batch=1,
            elapsed=1.5,
            attempts=2,
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=90,
            cache_creation_tokens=10,
        )
        call = p.calls[0]
        assert (call.label, call.batch, call.attempts) == ("security", 1, 2)
        assert (call.cache_read_tokens, call.cache_creation_tokens) == (90, 10)

    def test_reset_clears_prior_records(self) -> None:
        p = Profiler()
        with p.stage("redact"):
            pass
        p.record_call(
            label="x",
            batch=1,
            elapsed=0.1,
            attempts=1,
            input_tokens=1,
            output_tokens=1,
            cache_read_tokens=0,
            cache_creation_tokens=0,
        )
        p.reset()
        assert p.calls == [] and p.stages == []

    def test_render_summarises_stages_calls_and_cache_totals(self) -> None:
        p = Profiler()
        with p.stage("review"):
            pass
        p.record_call(
            label="security",
            batch=1,
            elapsed=2.0,
            attempts=1,
            input_tokens=1000,
            output_tokens=50,
            cache_read_tokens=800,
            cache_creation_tokens=200,
        )
        p.record_call(
            label="tests",
            batch=1,
            elapsed=5.0,
            attempts=3,
            input_tokens=1000,
            output_tokens=50,
            cache_read_tokens=100,
            cache_creation_tokens=0,
            error="RateLimitError: too many requests",
        )
        text = p.render()
        assert "total wall time" in text
        assert "review" in text
        # Calls are sorted by elapsed, slowest first.
        assert text.index("tests") < text.index("security")
        assert "RateLimitError" in text
        assert "900 tokens read / 200 created across 2 calls" in text


class TestTimedComplete:
    def test_success_records_the_result_usage(self) -> None:
        provider = FakeProvider(
            result=ProviderResult(
                text="{}",
                input_tokens=42,
                output_tokens=7,
                cache_read_tokens=30,
                cache_creation_tokens=12,
                attempts=2,
            )
        )
        timed_complete(provider, [{"role": "user", "content": "hi"}], model="m", label="reflect")
        call = profiler.calls[-1]
        assert call.label == "reflect"
        assert call.attempts == 2
        assert (call.input_tokens, call.cache_read_tokens) == (42, 30)
        assert call.error is None

    def test_failure_records_then_reraises(self) -> None:
        class _Boom(FakeProvider):
            def complete(self, messages, model, **opts):  # type: ignore[override]
                raise RuntimeError("nope")

        with pytest.raises(RuntimeError):
            timed_complete(_Boom(), [{"role": "user", "content": "hi"}], model="m", label="triage")
        call = profiler.calls[-1]
        assert call.label == "triage"
        assert call.error == "RuntimeError"


class TestEnginePipelineTiming:
    def test_review_records_stages_and_per_lens_calls(self) -> None:
        cfg = ReviewConfig(
            provider=Provider.ollama,
            model="llama3",
            categories=[ReviewCategory.security, ReviewCategory.performance],
            reflect=False,
        )
        provider = FakeProvider(
            result=ProviderResult(
                text=json.dumps({"findings": []}), input_tokens=9, output_tokens=1
            )
        )
        LLMReviewEngine(provider).review(_CTX, cfg)

        stage_names = [s.name for s in profiler.stages]
        expected_stages = ("redact", "split", "expand", "batch", "review", "snap", "dedupe")
        for expected in (*expected_stages, "filter"):
            assert expected in stage_names, f"missing stage {expected!r}"

        labels = sorted(c.label for c in profiler.calls)
        assert labels == ["performance", "security"]
        assert all(c.batch == 1 for c in profiler.calls)
        assert all(c.error is None for c in profiler.calls)

    def test_failed_lens_call_is_recorded_with_its_reason(self) -> None:
        class _OneBadLens(FakeProvider):
            def complete(self, messages, model, **opts):  # type: ignore[override]
                raise RuntimeError("quota exhausted")

        cfg = ReviewConfig(
            provider=Provider.ollama,
            model="llama3",
            categories=[ReviewCategory.security],
            reflect=False,
        )
        import lgtmaybe.engine.engine as engine_mod

        with pytest.raises(engine_mod.ReviewIncompleteError):
            LLMReviewEngine(_OneBadLens()).review(_CTX, cfg)
        call = profiler.calls[-1]
        assert call.error is not None and "quota exhausted" in call.error
        assert call.attempts == 0  # unknown on the exception path
