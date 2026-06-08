"""Runner wiring test — fixture load → engine → score, with a fake provider."""

from __future__ import annotations

import json

import pytest

from evals import run as run_mod
from lgtmaybe.core.models import ProviderResult, ReviewFinding, Severity
from tests.fakes import FakeProvider


class _ShellInjectionProvider(FakeProvider):
    """Returns the badcode shell-injection finding for every review call."""

    def complete(self, messages, model, **opts):  # type: ignore[override]
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        finding = ReviewFinding(
            path="badcode.py",
            line=30,
            severity=Severity.high,
            title="Command injection via shell=True",
            body="report_name is concatenated into a shell command.",
        )
        return ProviderResult(
            text=json.dumps({"findings": [finding.model_dump(mode="json")]}),
            input_tokens=1,
            output_tokens=1,
        )


def test_runner_loads_fixtures_and_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _ShellInjectionProvider())
    # The provider catches 1 of the 5 planted issues — passes a low bar, fails a high one.
    assert run_mod.main(["--provider", "ollama", "--model", "x", "--min-recall", "0.0"]) == 0
    assert run_mod.main(["--provider", "ollama", "--model", "x", "--min-recall", "0.9"]) == 1


def test_runner_fails_when_review_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Unparseable(FakeProvider):
        def complete(self, messages, model, **opts):  # type: ignore[override]
            self.calls.append({"messages": messages, "model": model, "opts": opts})
            return ProviderResult(text="I cannot help with that.", input_tokens=1, output_tokens=1)

    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _Unparseable())
    assert run_mod.main(["--provider", "ollama", "--model", "x", "--min-recall", "0.0"]) == 1


def _capture_build_provider(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Patch build_provider to record its kwargs and serve the catch-one provider."""
    calls: list[dict[str, object]] = []

    def fake_build(*_args: object, **kwargs: object) -> _ShellInjectionProvider:
        calls.append(kwargs)
        return _ShellInjectionProvider()

    monkeypatch.setattr(run_mod, "build_provider", fake_build)
    return calls


def test_timeout_and_num_ctx_thread_to_build_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """--timeout and --num-ctx reach build_provider so big ollama diffs get more room."""
    calls = _capture_build_provider(monkeypatch)

    run_mod.main(
        [
            "--provider",
            "ollama",
            "--model",
            "x",
            "--min-recall",
            "0.0",
            "--timeout",
            "600",
            "--num-ctx",
            "32768",
        ]
    )

    assert calls, "build_provider was never called"
    assert calls[0]["timeout"] == 600
    assert calls[0]["num_ctx"] == 32768


def test_num_ctx_is_omitted_for_hosted_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """num_ctx is ollama-only — litellm rejects it for hosted providers, so don't send it."""
    calls = _capture_build_provider(monkeypatch)

    run_mod.main(
        ["--provider", "openai", "--model", "x", "--min-recall", "0.0", "--num-ctx", "9000"]
    )

    assert calls
    assert "num_ctx" not in calls[0]


def test_max_input_tokens_threads_to_review_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """--max-input-tokens reaches the ReviewConfig the engine batches against."""
    seen: list[int] = []

    real_review = run_mod.LLMReviewEngine.review

    def spy_review(self, ctx, cfg):  # type: ignore[no-untyped-def]
        seen.append(cfg.max_input_tokens)
        return real_review(self, ctx, cfg)

    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _ShellInjectionProvider())
    monkeypatch.setattr(run_mod.LLMReviewEngine, "review", spy_review)

    run_mod.main(
        [
            "--provider",
            "ollama",
            "--model",
            "x",
            "--min-recall",
            "0.0",
            "--max-input-tokens",
            "250000",
        ]
    )

    assert seen and all(v == 250000 for v in seen)


def test_reflect_defaults_on_and_no_reflect_disables_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """--no-reflect turns off the reflection pass (weak CI models over-prune otherwise)."""
    seen: list[bool] = []

    real_review = run_mod.LLMReviewEngine.review

    def spy_review(self, ctx, cfg):  # type: ignore[no-untyped-def]
        seen.append(cfg.reflect)
        return real_review(self, ctx, cfg)

    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _ShellInjectionProvider())
    monkeypatch.setattr(run_mod.LLMReviewEngine, "review", spy_review)

    run_mod.main(["--provider", "ollama", "--model", "x", "--min-recall", "0.0"])
    assert seen and all(v is True for v in seen)

    seen.clear()
    run_mod.main(["--provider", "ollama", "--model", "x", "--min-recall", "0.0", "--no-reflect"])
    assert seen and all(v is False for v in seen)
