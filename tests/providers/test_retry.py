"""Tests for retry + fallback behaviour in LiteLLMProvider."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import litellm
import pytest

from lgtmaybe.core.models import attempts_of
from lgtmaybe.providers import litellm_provider as provider_module
from lgtmaybe.providers.litellm_provider import _MAX_ATTEMPTS, LiteLLMProvider


def _fake_response(content: str = "ok") -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=10),
    )


class TestRetry:
    def test_sdk_hang_cannot_exceed_the_request_timeout(self) -> None:
        """The adapter timeout is a real wall-clock bound even if LiteLLM's
        downstream transport never returns."""
        release = threading.Event()

        def hangs(*args: Any, **kwargs: Any) -> Any:
            release.wait(timeout=5)
            return _fake_response()

        started = time.perf_counter()
        try:
            with (
                patch("litellm.completion", side_effect=hangs),
                patch("lgtmaybe.providers.litellm_provider._MAX_ATTEMPTS", 1),
            ):
                provider = LiteLLMProvider(timeout=0.05)
                with pytest.raises(TimeoutError, match="exceeded 0.05"):
                    provider.complete([{"role": "user", "content": "hi"}], "openrouter/deepseek")
        finally:
            release.set()

        assert time.perf_counter() - started < 0.5

    def test_timeout_error_reports_the_measured_wait(self) -> None:
        """The bound is decided by the monotonic clock, so the error says how long
        we actually waited — an overshoot on a coarse-timer platform (Windows'
        ~15.6ms granularity) is then visible instead of silent."""
        release = threading.Event()

        def hangs(*args: Any, **kwargs: Any) -> Any:
            release.wait(timeout=5)
            return _fake_response()

        try:
            with (
                patch("litellm.completion", side_effect=hangs),
                patch("lgtmaybe.providers.litellm_provider._MAX_ATTEMPTS", 1),
            ):
                provider = LiteLLMProvider(timeout=0.05)
                with pytest.raises(TimeoutError, match=r"exceeded 0.05s \(waited \d+\.\d+s\)"):
                    provider.complete([{"role": "user", "content": "hi"}], "openrouter/deepseek")
        finally:
            release.set()

    def test_call_completing_inside_the_timer_slop_is_not_discarded(self) -> None:
        """A call that finished just past the deadline is honoured, not thrown away.

        Discarding it would waste a response the provider already billed for, and
        (worse) replace its real error — a quota 429, a bad key — with a bare
        timeout that says nothing about what to fix. Driven with a fake clock and
        an inline worker so it pins the behaviour deterministically rather than
        racing a real deadline.
        """
        # start=0.0, then every check is past the 0.05s deadline.
        clock = iter([0.0] + [100.0] * 20)

        class _InlineThread:
            """Runs the worker inline, so the future is complete before the wait."""

            def __init__(self, target: Any, **_: Any) -> None:
                self._target = target

            def start(self) -> None:
                self._target()

        with (
            patch("litellm.completion", return_value=_fake_response("late but complete")),
            patch.object(provider_module, "time", SimpleNamespace(monotonic=lambda: next(clock))),
            patch.object(provider_module.threading, "Thread", _InlineThread),
        ):
            response = provider_module._completion_with_wall_timeout(
                "openai/gpt-4o", [{"role": "user", "content": "hi"}], {"timeout": 0.05}
            )

        assert response.choices[0].message.content == "late but complete"

    def test_permanent_error_is_not_converted_into_a_retryable_timeout(self) -> None:
        """A permanent failure must surface as itself, not as a timeout: the review's
        failure notice quotes the error, and "provider request exceeded 60s" tells
        a user nothing about an invalid API key."""
        calls = 0

        def bad_auth(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise litellm.AuthenticationError(
                message="AuthenticationError: invalid api key",
                model="gpt-4o",
                llm_provider="openai",
            )

        with patch("litellm.completion", side_effect=bad_auth):
            provider = LiteLLMProvider(timeout=0.05)
            with pytest.raises(litellm.AuthenticationError):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert calls == 1

    def test_first_call_raises_then_retry_succeeds(self) -> None:
        """Provider retries on transient failure and returns the good result."""
        good_response = _fake_response("retried ok")
        call_count = 0

        def flaky(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")
            return good_response

        with patch("litellm.completion", side_effect=flaky):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert result.text == "retried ok"
        assert call_count == 2
        # The retry is visible on the result, for the timing instrumentation.
        assert result.attempts == 2

    def test_first_try_success_reports_one_attempt(self) -> None:
        with patch("litellm.completion", return_value=_fake_response("ok")):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")
        assert result.attempts == 1

    def test_all_retries_exhausted_raises(self) -> None:
        """When all retries are exhausted the error propagates."""
        with patch("litellm.completion", side_effect=RuntimeError("always fails")):
            provider = LiteLLMProvider()
            with pytest.raises(RuntimeError):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

    def test_terminal_failure_carries_the_attempts_it_burned(self) -> None:
        """A failed call must say how many attempts it cost.

        The count only ever rode home on a successful ProviderResult, so a
        three-attempt failure was recorded as ``attempts=0`` — reading as
        "never retried" in the timing profile, while it had in fact burned the
        whole retry budget.
        """
        with patch("litellm.completion", side_effect=RuntimeError("always fails")):
            provider = LiteLLMProvider()
            with pytest.raises(RuntimeError) as caught:
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert attempts_of(caught.value) == _MAX_ATTEMPTS

    def test_fail_fast_error_reports_its_single_attempt(self) -> None:
        """A permanent error is tried once, and says so."""
        with patch(
            "litellm.completion",
            side_effect=litellm.AuthenticationError(
                message="invalid api key", model="gpt-4o", llm_provider="openai"
            ),
        ):
            provider = LiteLLMProvider()
            with pytest.raises(litellm.AuthenticationError) as caught:
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert attempts_of(caught.value) == 1

    def test_an_unstamped_exception_reports_no_attempts(self) -> None:
        """An error raised before the adapter ever counted an attempt stays 0."""
        assert attempts_of(RuntimeError("never reached the adapter")) == 0

    def test_a_failing_fallback_reports_both_models_requests(self) -> None:
        """Both legs' requests were billed, so a total failure counts both."""
        with patch("litellm.completion", side_effect=RuntimeError("both models fail")):
            provider = LiteLLMProvider(model="openrouter/slow", fallback_model="openrouter/quick")
            with pytest.raises(RuntimeError) as caught:
                provider.complete([{"role": "user", "content": "hi"}], "ignored")

        assert attempts_of(caught.value) == 2 * _MAX_ATTEMPTS

    def test_a_re_send_inside_one_attempt_still_counts(self) -> None:
        """Every request that goes out counts, not every tenacity attempt.

        The empty-structured-output path issues a SECOND model request inside one
        attempt. Counting attempts rather than requests reported that failure as
        one call when the provider had been asked twice.
        """
        calls = 0

        def empty_then_fail(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if "response_format" in kwargs:
                return _fake_response("")  # schema decoder yielded nothing
            raise litellm.AuthenticationError(
                message="invalid api key", model="gpt-4o", llm_provider="openai"
            )

        with patch("litellm.completion", side_effect=empty_then_fail):
            provider = LiteLLMProvider()
            with pytest.raises(litellm.AuthenticationError) as caught:
                provider.complete(
                    [{"role": "user", "content": "hi"}], "openai/gpt-4o", response_format={}
                )

        assert calls == 2
        assert attempts_of(caught.value) == 2


class TestWallTimeoutIsNotRetried:
    """A wall-clock timeout re-sent unchanged cannot do anything but burn budget.

    The request, the model and the wall are all identical on the retry, so
    attempts 2..N buy nothing and cost another full timeout each — at the
    generous 1800s default that is an hour of runner time per lens before the
    failure surfaces.
    """

    def test_the_wall_timeout_is_attempted_once(self) -> None:
        # The request runs on a daemon thread the wall timeout abandons, so the
        # counter is read only after that thread confirms it started — otherwise a
        # loaded runner could time out before the worker was ever scheduled and
        # the assertion would be about thread scheduling, not about retries.
        release = threading.Event()
        started = threading.Event()
        counted = threading.Lock()
        calls = 0

        def hangs(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            with counted:
                calls += 1
            started.set()
            release.wait(timeout=5)
            return _fake_response()

        try:
            with patch("litellm.completion", side_effect=hangs):
                provider = LiteLLMProvider(timeout=0.05)
                with pytest.raises(provider_module.ProviderWallTimeout, match="exceeded 0.05"):
                    provider.complete([{"role": "user", "content": "hi"}], "openrouter/deepseek")
        finally:
            release.set()

        assert started.wait(timeout=5), "the one request never reached the provider"
        with counted:
            assert calls == 1

    def test_it_still_reads_as_a_timeout_error(self) -> None:
        """Callers (and the review's failure notice) keep seeing a TimeoutError."""
        assert issubclass(provider_module.ProviderWallTimeout, TimeoutError)

    def test_a_transport_timeout_is_still_retried(self) -> None:
        """litellm's own connect/read timeout is a blip, not a blown wall — it
        keeps its retries."""
        calls = 0

        def transport_timeout(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise litellm.Timeout(
                message="Connection timed out", model="deepseek", llm_provider="openrouter"
            )

        with patch("litellm.completion", side_effect=transport_timeout):
            provider = LiteLLMProvider()
            with pytest.raises(litellm.Timeout):
                provider.complete([{"role": "user", "content": "hi"}], "openrouter/deepseek")

        assert calls == _MAX_ATTEMPTS

    def test_the_fallback_model_still_gets_its_chance(self) -> None:
        """Not retrying is about re-sending the SAME request; a different model is
        a different request, and still worth trying."""
        release = threading.Event()
        seen: list[str] = []

        def hang_then_answer(*args: Any, **kwargs: Any) -> Any:
            seen.append(kwargs["model"])
            if kwargs["model"] == "openrouter/slow":
                release.wait(timeout=5)
            return _fake_response("fallback answered")

        try:
            with patch("litellm.completion", side_effect=hang_then_answer):
                provider = LiteLLMProvider(
                    model="openrouter/slow", fallback_model="openrouter/quick", timeout=0.05
                )
                result = provider.complete([{"role": "user", "content": "hi"}], "ignored")
        finally:
            release.set()

        assert result.text == "fallback answered"
        assert seen == ["openrouter/slow", "openrouter/quick"]
        # Two requests went out and both were billed, so both are in the total —
        # a fallback rescue reported as one request would hide the primary's
        # burnt budget entirely.
        assert result.attempts == 2


class TestTemperatureRejection:
    def test_retries_without_temperature_when_model_rejects_the_value(self) -> None:
        """Models like gpt-5.5 accept the temperature param but only the default
        value; on that rejection the provider retries without temperature."""
        good = _fake_response("ok without temperature")
        seen_temperatures: list[Any] = []

        def side_effect(*args: Any, **kwargs: Any) -> Any:
            seen_temperatures.append(kwargs.get("temperature", "absent"))
            if "temperature" in kwargs:
                raise RuntimeError(
                    "litellm.BadRequestError: OpenAIException - Unsupported value: "
                    "'temperature' does not support 0 with this model. Only the "
                    "default (1) value is supported."
                )
            return good

        with patch("litellm.completion", side_effect=side_effect):
            provider = LiteLLMProvider()
            result = provider.complete(
                [{"role": "user", "content": "hi"}], "openai/gpt-5.5", temperature=0.0
            )

        assert result.text == "ok without temperature"
        assert seen_temperatures == [0.0, "absent"]

    def test_unrelated_bad_request_is_not_swallowed(self) -> None:
        """A non-temperature error must still propagate, not be retried bare."""
        with patch("litellm.completion", side_effect=RuntimeError("invalid api key")):
            provider = LiteLLMProvider()
            with pytest.raises(RuntimeError, match="invalid api key"):
                provider.complete(
                    [{"role": "user", "content": "hi"}], "openai/gpt-5.5", temperature=0.0
                )


class TestEmptyStructuredOutputFallback:
    """Some grammar-constrained backends (LM Studio fronting a thinking qwen
    model) return empty content under a response_format JSON schema. The provider
    drops the schema and retries once so the model can emit parseable text."""

    def test_empty_content_under_response_format_retries_without_it(self) -> None:
        seen_response_format: list[Any] = []

        def side_effect(*args: Any, **kwargs: Any) -> Any:
            seen_response_format.append("response_format" in kwargs)
            if "response_format" in kwargs:
                return _fake_response("")  # schema mode yields nothing
            return _fake_response('{"findings": []}')

        with patch("litellm.completion", side_effect=side_effect):
            provider = LiteLLMProvider()
            result = provider.complete(
                [{"role": "user", "content": "hi"}],
                "openai/qwen",
                response_format={"type": "json_schema"},
            )

        assert result.text == '{"findings": []}'
        assert seen_response_format == [True, False]

    def test_non_empty_content_keeps_response_format(self) -> None:
        calls = 0

        def side_effect(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            return _fake_response('{"findings": []}')

        with patch("litellm.completion", side_effect=side_effect):
            provider = LiteLLMProvider()
            result = provider.complete(
                [{"role": "user", "content": "hi"}],
                "openai/gpt-4o",
                response_format={"type": "json_schema"},
            )

        assert result.text == '{"findings": []}'
        assert calls == 1  # good content first time → no retry

    def test_empty_once_makes_later_calls_skip_response_format_up_front(self) -> None:
        """After one empty structured response, the provider remembers the model's
        schema mode is broken and drops response_format up front on later calls —
        so a slow local model isn't billed a wasted empty round-trip every time."""
        seen_response_format: list[bool] = []

        def side_effect(*args: Any, **kwargs: Any) -> Any:
            has_rf = "response_format" in kwargs
            seen_response_format.append(has_rf)
            if has_rf:
                return _fake_response("")  # schema mode yields nothing
            return _fake_response('{"findings": []}')

        with patch("litellm.completion", side_effect=side_effect):
            provider = LiteLLMProvider()
            provider.complete(
                [{"role": "user", "content": "a"}], "openai/qwen", response_format={"x": 1}
            )
            provider.complete(
                [{"role": "user", "content": "b"}], "openai/qwen", response_format={"x": 1}
            )

        # First call: with-rf (empty) then without (good). Second call: straight
        # to without-rf — no wasted empty round-trip.
        assert seen_response_format == [True, False, False]

    def test_empty_content_without_response_format_is_not_retried(self) -> None:
        """A genuinely empty answer with no schema to drop is returned as-is — no
        infinite retry loop."""
        calls = 0

        def side_effect(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            return _fake_response("")

        with patch("litellm.completion", side_effect=side_effect):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert result.text == ""
        assert calls == 1


class TestFailFastOnPermanentErrors:
    """Retrying a permanent error can't fix it — and stacked backoff over every
    lens turns an instant "out of credit" into many minutes of burned runner
    time (the gpt-5.5 quota failure that took ~13 min). Permanent errors must
    fail fast; only genuinely transient ones get the exponential backoff."""

    def test_quota_rate_limit_is_not_retried(self) -> None:
        """OpenAI's insufficient_quota 429 ("exceeded your current quota") is a
        billing dead-end — one attempt, then raise."""
        calls = 0

        def quota(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise litellm.RateLimitError(
                message=(
                    "litellm.RateLimitError: OpenAIException - You exceeded your "
                    "current quota, please check your plan and billing details."
                ),
                model="gpt-5.5",
                llm_provider="openai",
            )

        with patch("litellm.completion", side_effect=quota):
            provider = LiteLLMProvider()
            with pytest.raises(litellm.RateLimitError):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-5.5")

        assert calls == 1

    def test_transient_rate_limit_is_still_retried(self) -> None:
        """A plain capacity rate-limit (not quota) is transient — keep the
        exponential backoff and retry it."""
        good = _fake_response("ok after backoff")
        calls = 0

        def flaky(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise litellm.RateLimitError(
                    message="RateLimitError: OpenAIException - Rate limit reached for gpt-4o",
                    model="gpt-4o",
                    llm_provider="openai",
                )
            return good

        with patch("litellm.completion", side_effect=flaky):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert result.text == "ok after backoff"
        assert calls == 2

    def test_expired_cloud_credentials_is_not_retried(self) -> None:
        """An expired AWS security token (Bedrock 403) reaches litellm as an
        APIConnectionError, not an AuthenticationError — but retrying can't
        refresh it, so it's a permanent failure: one attempt, then raise. The
        message is what distinguishes it from a genuinely transient connection
        error (ollama warming up), which must still be retried."""
        calls = 0

        def expired(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise litellm.APIConnectionError(
                message=(
                    "litellm.APIConnectionError: BedrockException - "
                    '{"message":"The security token included in the request is expired"}'
                ),
                model="us.anthropic.claude-sonnet-4-6",
                llm_provider="bedrock",
            )

        with patch("litellm.completion", side_effect=expired):
            provider = LiteLLMProvider()
            with pytest.raises(litellm.APIConnectionError):
                provider.complete(
                    [{"role": "user", "content": "hi"}],
                    "bedrock/us.anthropic.claude-sonnet-4-6",
                )

        assert calls == 1

    def test_openrouter_insufficient_credits_is_not_retried(self) -> None:
        """OpenRouter reserves prompt + max_tokens against the balance BEFORE
        generating, and refuses the request when the balance can't cover it. That
        is a billing dead-end exactly like OpenAI's insufficient_quota — but it
        arrives as a generic APIError, not a RateLimitError, so the type check
        alone misses it and every lens retried it three times."""
        calls = 0

        def no_credit(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise litellm.APIError(
                status_code=402,
                message=(
                    "litellm.APIError: APIError: OpenrouterException - "
                    '{"error":{"message":"This request requires more credits, or fewer '
                    "max_tokens. You requested up to 65536 tokens, but can only afford "
                    '25905."}}'
                ),
                model="vendor/m",
                llm_provider="openrouter",
            )

        with patch("litellm.completion", side_effect=no_credit):
            provider = LiteLLMProvider()
            with pytest.raises(litellm.APIError):
                provider.complete([{"role": "user", "content": "hi"}], "openrouter/vendor/m")

        assert calls == 1

    def test_transient_api_error_is_still_retried(self) -> None:
        """A plain APIError (an upstream blip) stays transient — only the
        credit-exhaustion message makes one permanent."""
        good = _fake_response("ok after backoff")
        calls = 0

        def flaky(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise litellm.APIError(
                    status_code=500,
                    message="litellm.APIError: APIError: OpenrouterException - upstream error",
                    model="vendor/m",
                    llm_provider="openrouter",
                )
            return good

        with patch("litellm.completion", side_effect=flaky):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "openrouter/vendor/m")

        assert result.text == "ok after backoff"
        assert calls == 2

    def test_authentication_error_is_not_retried(self) -> None:
        """A bad key won't become good on a retry — fail fast."""
        calls = 0

        def bad_auth(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise litellm.AuthenticationError(
                message="AuthenticationError: invalid api key",
                model="gpt-4o",
                llm_provider="openai",
            )

        with patch("litellm.completion", side_effect=bad_auth):
            provider = LiteLLMProvider()
            with pytest.raises(litellm.AuthenticationError):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert calls == 1

    def test_not_found_error_is_not_retried(self) -> None:
        """An unknown model won't be found on a retry — fail fast, one attempt."""
        calls = 0

        def not_found(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise litellm.NotFoundError(
                message="NotFoundError: model does not exist",
                model="gpt-9000",
                llm_provider="openai",
            )

        with patch("litellm.completion", side_effect=not_found):
            provider = LiteLLMProvider()
            with pytest.raises(litellm.NotFoundError):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-9000")

        assert calls == 1

    def test_permission_denied_error_is_not_retried(self) -> None:
        """A denied permission won't be granted on a retry — fail fast."""
        calls = 0

        def denied(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise litellm.exceptions.PermissionDeniedError(
                message="PermissionDeniedError: access denied",
                model="gpt-4o",
                llm_provider="openai",
                response=httpx.Response(403, request=httpx.Request("POST", "https://api")),
            )

        with patch("litellm.completion", side_effect=denied):
            provider = LiteLLMProvider()
            with pytest.raises(litellm.exceptions.PermissionDeniedError):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert calls == 1

    def test_transient_error_retries_up_to_max_attempts(self) -> None:
        """A transient error is retried, but no more than _MAX_ATTEMPTS times."""
        calls = 0

        def always_transient(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise RuntimeError("transient")

        with patch("litellm.completion", side_effect=always_transient):
            provider = LiteLLMProvider()
            with pytest.raises(RuntimeError):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert calls == _MAX_ATTEMPTS

    def test_attempts_share_a_wall_clock_budget_derived_from_the_timeout(self) -> None:
        """Retries stop once 2.5× the per-request timeout is spent, even before
        the attempt cap — a flaky model must not burn attempts × timeout +
        backoff per call. With a 0.05s timeout the budget is 0.125s, which one
        slow failing attempt plus the first backoff already exceeds."""
        import time

        calls = 0

        def slow_transient(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            # Must outlast the 0.05s wall bound by more than the platform's timer
            # granularity, or `future.result(timeout=...)` can over-wait, see the
            # call already finished, and surface this RuntimeError instead of the
            # TimeoutError under test — 0.06s lost that race on Windows (~15.6ms
            # granularity). The wall timeout abandons the worker thread rather
            # than joining it, so a long sleep costs no test time.
            time.sleep(1.0)
            raise RuntimeError("transient")

        with patch("litellm.completion", side_effect=slow_transient):
            provider = LiteLLMProvider(timeout=0.05)
            with pytest.raises(TimeoutError, match="exceeded 0.05"):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert calls < _MAX_ATTEMPTS

    def test_quota_error_does_not_storm_the_fallback_either(self) -> None:
        """Fail-fast applies to the fallback leg too: each model is tried once,
        no per-model retry storm."""
        calls = 0

        def quota(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise litellm.RateLimitError(
                message="OpenAIException - You exceeded your current quota",
                model="m",
                llm_provider="openai",
            )

        with patch("litellm.completion", side_effect=quota):
            provider = LiteLLMProvider(fallback_model="openai/gpt-4o-mini")
            with pytest.raises(litellm.RateLimitError):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-5.5")

        assert calls == 2  # primary once + fallback once

    def test_ollama_connection_error_is_retried(self) -> None:
        """ollama gets the same treatment: a transient connection error (the
        local server still warming up) is retried, not failed fast."""
        good = _fake_response("ok")
        calls = 0

        def flaky(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise litellm.APIConnectionError(
                    message="Connection refused",
                    model="ollama/qwen3",
                    llm_provider="ollama",
                )
            return good

        with patch("litellm.completion", side_effect=flaky):
            provider = LiteLLMProvider()
            result = provider.complete([{"role": "user", "content": "hi"}], "ollama/qwen3")

        assert result.text == "ok"
        assert calls == 2

    def test_litellm_internal_retries_are_disabled(self) -> None:
        """We own retries via tenacity; litellm's own retry loop is switched off
        so a failure isn't ground through two stacked backoff layers."""
        response = _fake_response()
        with patch("litellm.completion", return_value=response) as mock_completion:
            provider = LiteLLMProvider()
            provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")

        assert mock_completion.call_args.kwargs.get("num_retries") == 0


class TestFallback:
    def test_primary_fails_hard_fallback_model_is_used(self) -> None:
        """After primary exhausts retries, fallback model is tried."""
        fallback_response = _fake_response("fallback result")

        primary_model = "openai/gpt-4o"
        fallback_model = "openai/gpt-3.5-turbo"

        called_with_models: list[str] = []

        def side_effect(*args: Any, **kwargs: Any) -> Any:
            model = kwargs.get("model", args[0] if args else "")
            called_with_models.append(model)
            if model == primary_model:
                raise RuntimeError("primary dead")
            return fallback_response

        with patch("litellm.completion", side_effect=side_effect):
            provider = LiteLLMProvider(fallback_model=fallback_model)
            result = provider.complete([{"role": "user", "content": "hi"}], primary_model)

        assert result.text == "fallback result"
        assert fallback_model in called_with_models

    def test_no_fallback_configured_raises_on_hard_failure(self) -> None:
        """Without a fallback, hard failure propagates."""
        with patch("litellm.completion", side_effect=RuntimeError("dead")):
            provider = LiteLLMProvider()
            with pytest.raises(RuntimeError):
                provider.complete([{"role": "user", "content": "hi"}], "openai/gpt-4o")
