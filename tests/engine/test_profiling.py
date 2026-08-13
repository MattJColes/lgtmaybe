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
    stamp_attempts,
)
from lgtmaybe.core.ports import ProviderTruncated
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

    def test_record_call_captures_reasoning_tokens(self) -> None:
        """A lens that wrote 50 tokens in 40 seconds needs an explanation, and
        the thinking it did before writing them is that explanation."""
        p = Profiler()
        p.record_call(
            label="security",
            batch=1,
            elapsed=1.5,
            attempts=1,
            input_tokens=100,
            output_tokens=1200,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            reasoning_tokens=1100,
        )
        assert p.calls[0].reasoning_tokens == 1100

    def test_reasoning_tokens_default_to_unknown(self) -> None:
        """Non-reasoning routes report nothing, and callers may omit it — which
        records as None. A 0 would claim the model did no thinking; nobody said
        that, and the profile table would print it as if somebody had."""
        p = Profiler()
        p.record_call(
            label="security",
            batch=1,
            elapsed=1.5,
            attempts=1,
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=0,
            cache_creation_tokens=0,
        )
        assert p.calls[0].reasoning_tokens is None

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
        assert "tokens: 2,100 billable (2,000 in / 100 out) across 2 calls" in text

    def test_render_shows_the_reasoning_column(self) -> None:
        """The `--profile` table is where the reasoning budget becomes legible:
        out_tok next to think_tok is the whole comparison."""
        p = Profiler()
        p.record_call(
            label="security",
            batch=1,
            elapsed=2.0,
            attempts=1,
            input_tokens=1000,
            output_tokens=1250,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            reasoning_tokens=1100,
        )
        text = p.render()
        assert "think_tok" in text
        assert "1100" in text

    def test_render_omits_the_reasoning_line_when_no_route_reported_any(self) -> None:
        """Zero across the board means "this route does not report it", not "this
        model did no thinking" — so claim nothing rather than assert a false 0."""
        p = Profiler()
        p.record_call(
            label="security",
            batch=1,
            elapsed=2.0,
            attempts=1,
            input_tokens=1000,
            output_tokens=50,
            cache_read_tokens=0,
            cache_creation_tokens=0,
        )
        assert "reasoning:" not in p.render()

    def test_render_summarises_the_reasoning_share_of_output(self) -> None:
        """The share is the number the decision turns on, so compute it here
        rather than leave every reader to do the division by hand."""
        p = Profiler()
        for _ in range(2):
            p.record_call(
                label="security",
                batch=1,
                elapsed=2.0,
                attempts=1,
                input_tokens=1000,
                output_tokens=1000,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                reasoning_tokens=900,
            )
        assert "reasoning: 1,800 of 2,000 output tokens (90%)" in p.render()


class TestTotalTokens:
    """`total_tokens` backs the max_review_tokens ceiling, so it must count the
    one figure every provider route agrees on and nothing else."""

    def _record(self, p: Profiler, **usage: int) -> None:
        p.record_call(
            label="security",
            batch=1,
            elapsed=1.0,
            attempts=1,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_tokens", 0),
            cache_creation_tokens=usage.get("cache_creation_tokens", 0),
        )

    def test_empty_profiler_has_spent_nothing(self) -> None:
        assert Profiler().total_tokens() == 0

    def test_sums_input_and_output_across_calls(self) -> None:
        p = Profiler()
        self._record(p, input_tokens=1000, output_tokens=50)
        self._record(p, input_tokens=300, output_tokens=7)
        assert p.total_tokens() == 1357

    def test_cache_counters_are_not_added_on_top(self) -> None:
        """Routes disagree on whether a cached read sits inside the prompt count;
        adding it would double-count on some and not others."""
        p = Profiler()
        self._record(
            p,
            input_tokens=1000,
            output_tokens=50,
            cache_read_tokens=800,
            cache_creation_tokens=200,
        )
        assert p.total_tokens() == 1050

    def test_a_failed_call_contributes_its_recorded_zero(self) -> None:
        p = Profiler()
        p.record_call(
            label="tests",
            batch=1,
            elapsed=1.0,
            attempts=2,
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            error="RateLimitError",
        )
        assert p.total_tokens() == 0

    def test_reset_clears_the_running_total(self) -> None:
        p = Profiler()
        self._record(p, input_tokens=1000, output_tokens=50)
        p.reset()
        assert p.total_tokens() == 0


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

    def test_success_records_the_reasoning_tokens(self) -> None:
        """Reflection and triage are model calls too — a reasoning model spends
        the budget on them the same way, so they report it the same way."""
        provider = FakeProvider(
            result=ProviderResult(
                text="{}",
                input_tokens=42,
                output_tokens=700,
                reasoning_tokens=650,
            )
        )
        timed_complete(provider, [{"role": "user", "content": "hi"}], model="m", label="reflect")
        assert profiler.calls[-1].reasoning_tokens == 650

    def test_a_truncation_reports_the_tokens_it_burned(self) -> None:
        """A truncated call is usually the most expensive one in the run, and it
        was reported as free.

        `record_error` zeroed every counter on the reasoning that a failure has
        no usage — true of a connection error, which never reached the model, and
        false of a truncation, which reached it and generated all the way to the
        ceiling. A real run spent 32,768 output tokens on one and recorded 0.
        """
        exc = ProviderTruncated(
            "response hit the 32768-token `max_tokens` ceiling before finishing",
            text="",
            input_tokens=24039,
            output_tokens=32768,
            reasoning_tokens=516,
        )
        profiler.record_error("code-health", 1, 380.1, exc, "ProviderTruncated: …")
        call = profiler.calls[-1]
        assert (call.input_tokens, call.output_tokens) == (24039, 32768)
        assert call.reasoning_tokens == 516

    def test_a_truncation_is_charged_to_the_token_budget(self) -> None:
        """`total_tokens` backs `max_review_tokens`, so a runaway that reports
        zero is a runaway the spend ceiling cannot see — which inverts the whole
        point of having one."""
        exc = ProviderTruncated("ceiling", text="", input_tokens=1000, output_tokens=32768)
        profiler.record_error("code-health", 1, 380.1, exc)
        assert profiler.total_tokens() == 33768

    def test_a_failure_with_no_usage_still_reports_zero(self) -> None:
        """A connection error never reached the model, so it genuinely cost
        nothing — the change above must not invent usage for it."""
        profiler.record_error("security", 1, 0.5, RuntimeError("connection reset"))
        call = profiler.calls[-1]
        assert (call.input_tokens, call.output_tokens) == (0, 0)
        assert profiler.total_tokens() == 0

    def test_failure_records_then_reraises(self) -> None:
        class _Boom(FakeProvider):
            def complete(self, messages, model, **opts):  # type: ignore[override]
                raise RuntimeError("nope")

        with pytest.raises(RuntimeError):
            timed_complete(_Boom(), [{"role": "user", "content": "hi"}], model="m", label="triage")
        call = profiler.calls[-1]
        assert call.label == "triage"
        assert call.error == "RuntimeError"

    def test_failure_records_the_attempts_the_adapter_burned(self) -> None:
        """A stamped failure reports its real attempt count, not 0 — else a call
        that spent its whole retry budget looks like it was never retried."""

        class _BoomAfterRetries(FakeProvider):
            def complete(self, messages, model, **opts):  # type: ignore[override]
                exc = RuntimeError("nope")
                stamp_attempts(exc, 3)
                raise exc

        with pytest.raises(RuntimeError):
            timed_complete(
                _BoomAfterRetries(), [{"role": "user", "content": "hi"}], model="m", label="triage"
            )
        assert profiler.calls[-1].attempts == 3


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

    def test_per_lens_calls_record_their_reasoning_tokens(self) -> None:
        """The per-lens fan-out is the measurement that matters: it is the lens
        calls that truncate, so it is the lens calls that must report the split."""
        cfg = ReviewConfig(
            provider=Provider.ollama,
            model="llama3",
            categories=[ReviewCategory.security],
            reflect=False,
        )
        provider = FakeProvider(
            result=ProviderResult(
                text=json.dumps({"findings": []}),
                input_tokens=900,
                output_tokens=1200,
                reasoning_tokens=1100,
            )
        )
        LLMReviewEngine(provider).review(_CTX, cfg)

        assert [c.reasoning_tokens for c in profiler.calls] == [1100]

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
        assert call.attempts == 0  # unstamped: it never reached the adapter's retry loop

    def test_failed_lens_call_reports_the_attempts_it_burned(self) -> None:
        """The per-lens recorder reads the count the adapter stamped, so a
        budget-burning timeout is distinguishable from a first-try failure."""

        class _BurntBudget(FakeProvider):
            def complete(self, messages, model, **opts):  # type: ignore[override]
                exc = TimeoutError("provider request exceeded 1800s (waited 1800.001s)")
                stamp_attempts(exc, 2)
                raise exc

        cfg = ReviewConfig(
            provider=Provider.openrouter,
            model="deepseek/deepseek-v4-pro",
            categories=[ReviewCategory.security],
            reflect=False,
        )
        import lgtmaybe.engine.engine as engine_mod

        with pytest.raises(engine_mod.ReviewIncompleteError):
            LLMReviewEngine(_BurntBudget()).review(_CTX, cfg)
        assert profiler.calls[-1].attempts == 2


class TestCeilingHitsReachTheProfile:
    """The row for a call that spent its whole output ceiling must say so.

    This is the seam the fix is for: the adapter classifies the ceiling hit, the
    profiler renders it. A local benchmark saw two lens calls report exactly the
    configured 512 output tokens with an empty error column, so the tooling
    reading that column counted zero truncations and could not exclude the call
    time — the row looked like a clean, cheap success.
    """

    @staticmethod
    def _at_ceiling(completion_tokens: int) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"findings": [{"path": "a.py"'),
                    # `stop`, not `length` — the case the profile used to miss.
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=900, completion_tokens=completion_tokens),
        )

    def test_a_ceiling_hit_without_a_finish_reason_is_marked(self) -> None:
        from unittest.mock import patch

        from lgtmaybe.providers.litellm_provider import LiteLLMProvider

        profiler.reset()
        provider = LiteLLMProvider(max_tokens=512)
        with (
            patch("litellm.completion", return_value=self._at_ceiling(512)),
            pytest.raises(ProviderTruncated),
        ):
            timed_complete(
                provider,
                [{"role": "user", "content": "hi"}],
                model="ollama/qwen2.5-coder:3b",
                label="ponytail",
            )

        call = profiler.calls[-1]
        assert call.error is not None, "the profile row gave no truncation marker"
        assert "Truncated" in call.error
        # And it is charged for what it spent: the row a reader is looking at is
        # routinely the most expensive call in the run.
        assert (call.input_tokens, call.output_tokens) == (900, 512)

    def test_the_rendered_error_column_is_not_a_dash(self) -> None:
        """Asserted on the rendered table, because `-` in that column is exactly
        what the benchmark tooling read as "no truncation here"."""
        from unittest.mock import patch

        from lgtmaybe.providers.litellm_provider import LiteLLMProvider

        profiler.reset()
        provider = LiteLLMProvider(max_tokens=512)
        with (
            patch("litellm.completion", return_value=self._at_ceiling(512)),
            pytest.raises(ProviderTruncated),
        ):
            timed_complete(
                provider,
                [{"role": "user", "content": "hi"}],
                model="ollama/qwen2.5-coder:3b",
                label="ponytail",
            )

        row = next(line for line in profiler.render().splitlines() if line.startswith("ponytail"))
        assert not row.rstrip().endswith("-"), f"error column read as empty: {row!r}"
        assert "Truncated" in row

    def test_a_normal_answer_still_renders_clean(self) -> None:
        """The guard must not paint every local call as truncated."""
        from unittest.mock import patch

        from lgtmaybe.providers.litellm_provider import LiteLLMProvider

        profiler.reset()
        provider = LiteLLMProvider(max_tokens=512)
        with patch("litellm.completion", return_value=self._at_ceiling(200)):
            timed_complete(
                provider,
                [{"role": "user", "content": "hi"}],
                model="ollama/qwen2.5-coder:3b",
                label="ponytail",
            )

        assert profiler.calls[-1].error is None


class TestReasoningShareIsLegibleFromAnyRun:
    """`max_tokens` pays for thinking AND answering, so the two settings are
    coupled — but the split only became visible when a call FAILED, in the
    truncation message. A healthy call that came within a few hundred tokens of
    the ceiling looked identical to one that used a fifth of it, which is why
    settling `reasoning_effort` needed a bespoke four-run experiment.
    """

    def test_a_route_that_reports_no_breakdown_renders_as_unknown_not_zero(self) -> None:
        """A zero would claim the model did no thinking. It said nothing."""
        p = Profiler()
        p.record_call(
            label="security",
            batch=1,
            elapsed=1.5,
            attempts=1,
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=0,
            cache_creation_tokens=0,
        )

        assert p.calls[0].reasoning_tokens is None
        row = next(line for line in p.render().splitlines() if line.startswith("security"))
        assert " 0 " not in row.split("20", 1)[1][:12]
        assert "-" in row

    def test_a_call_reports_its_reasoning_as_a_share_of_the_ceiling(self) -> None:
        """The ratio is the number that answers "is there headroom?" — neither
        raw count says it, and computing it by hand is what this replaces."""
        p = Profiler()
        p.record_call(
            label="security",
            batch=1,
            elapsed=1.5,
            attempts=1,
            input_tokens=100,
            output_tokens=4000,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            reasoning_tokens=4096,
            output_ceiling=8192,
        )

        row = next(line for line in p.render().splitlines() if line.startswith("security"))
        assert "4096" in row
        assert "50%" in row

    def test_the_summary_names_the_largest_share_seen(self) -> None:
        """So the headroom question is answered without reading every row."""
        p = Profiler()
        for label, reasoning in (("security", 800), ("artefacts", 7000)):
            p.record_call(
                label=label,
                batch=1,
                elapsed=1.0,
                attempts=1,
                input_tokens=10,
                output_tokens=reasoning + 100,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                reasoning_tokens=reasoning,
                output_ceiling=8192,
            )

        rendered = p.render()
        assert "largest" in rendered
        assert "85%" in rendered  # 7000 / 8192
        assert "artefacts" in rendered.split("largest", 1)[1]

    def test_the_aggregate_does_not_divide_by_unreported_calls(self) -> None:
        """Dividing known reasoning by EVERY call's output mixes a measurement
        with a silence, and understates the share by however much the
        unreporting calls generated — badly, when they generated most of it.

        The line says what it covered rather than leaving the reader to assume
        it covered everything."""
        p = Profiler()
        p.record_call(
            label="security",
            batch=1,
            elapsed=1.0,
            attempts=1,
            input_tokens=10,
            output_tokens=1000,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            reasoning_tokens=900,
        )
        p.record_call(  # a route that reports no breakdown, and generated a lot
            label="artefacts",
            batch=1,
            elapsed=1.0,
            attempts=1,
            input_tokens=10,
            output_tokens=9000,
            cache_read_tokens=0,
            cache_creation_tokens=0,
        )

        rendered = p.render()
        assert "reasoning: 900 of 1,000 output tokens (90%" in rendered  # not 9%
        assert "1 of 2 calls reporting it" in rendered

    def test_a_run_that_measured_zero_still_says_so(self) -> None:
        """A route that reported the breakdown and put 0 in it measured something.
        Suppressing the line puts that back in the same bucket as silence."""
        p = Profiler()
        p.record_call(
            label="security",
            batch=1,
            elapsed=1.0,
            attempts=1,
            input_tokens=10,
            output_tokens=500,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            reasoning_tokens=0,
        )

        assert "reasoning: 0 of 500 output tokens (0%)" in p.render()

    def test_no_share_is_claimed_when_no_ceiling_was_configured(self) -> None:
        """`max_tokens` unset means there is no denominator — a share against a
        number nobody chose would be invention, not accounting."""
        p = Profiler()
        p.record_call(
            label="security",
            batch=1,
            elapsed=1.0,
            attempts=1,
            input_tokens=10,
            output_tokens=900,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            reasoning_tokens=800,
        )

        rendered = p.render()
        assert "800" in rendered
        assert "largest" not in rendered
