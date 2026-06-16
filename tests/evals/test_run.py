"""Runner wiring test — fixture load → engine → score, with a fake provider."""

from __future__ import annotations

import json

import pytest

from evals import run as run_mod
from evals.scorer import FixtureScore
from lgtmaybe.core.models import ProviderResult, ReviewCategory, ReviewFinding, Severity
from tests.fakes import FakeProvider


def _score(name: str, matched: int, expected: int, *, parsed_ok: bool = True) -> FixtureScore:
    return FixtureScore(
        name=name,
        parsed_ok=parsed_ok,
        expected_count=expected,
        matched_count=matched,
        findings_count=matched,
        missed=[],
    )


def test_gate_pools_recall_across_fixtures() -> None:
    """A fixture dipping below the floor still passes if the pooled recall clears it.

    badcode at 2/7 (29%) is under a 0.3 floor on its own, but pooled with
    vibe-multifile at 5/11 the run is 7/18 = 39% — so one missed finding on a
    short fixture doesn't flip the job. Per-fixture gating would have failed here.
    """
    scores = [_score("badcode", 2, 7), _score("vibe-multifile", 5, 11)]
    ok, aggregate = run_mod._gate(scores, 0.3)
    assert ok
    assert aggregate == pytest.approx(7 / 18)


def test_gate_fails_when_pooled_recall_below_floor() -> None:
    scores = [_score("badcode", 1, 7), _score("vibe-multifile", 1, 11)]
    ok, _ = run_mod._gate(scores, 0.3)
    assert not ok


def test_gate_fails_on_any_parse_failure_regardless_of_recall() -> None:
    """A parse failure is a pipeline break, not model variance — it fails the run."""
    scores = [_score("badcode", 7, 7), _score("vibe-multifile", 0, 11, parsed_ok=False)]
    ok, _ = run_mod._gate(scores, 0.0)
    assert not ok


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
    # The provider catches 1 of the planted issues — passes a low bar, fails a high one.
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


def _capture_recursive(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> list[bool]:
    """Run main with *argv* and return the cfg.recursive seen by the engine."""
    seen: list[bool] = []
    real_review = run_mod.LLMReviewEngine.review

    def spy_review(self, ctx, cfg):  # type: ignore[no-untyped-def]
        seen.append(cfg.recursive)
        return real_review(self, ctx, cfg)

    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _ShellInjectionProvider())
    monkeypatch.setattr(run_mod.LLMReviewEngine, "review", spy_review)
    run_mod.main(argv)
    return seen


def test_recursive_defaults_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the flag the runner matches the engine default (recursive on)."""
    seen = _capture_recursive(
        monkeypatch,
        ["--provider", "ollama", "--model", "x", "--min-recall", "0.0", "--fixture", "rlm-bigfile"],
    )
    assert seen and all(v is True for v in seen)


def test_no_recursive_flag_pins_the_whole_file_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    """--no-recursive pins the original whole-file method so a run can A/B it."""
    seen = _capture_recursive(
        monkeypatch,
        [
            "--provider",
            "ollama",
            "--model",
            "x",
            "--min-recall",
            "0.0",
            "--fixture",
            "rlm-bigfile",
            "--no-recursive",
        ],
    )
    assert seen and all(v is False for v in seen)


def test_fixture_flag_selects_only_the_named_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    """--fixture scopes the run to the named fixture(s) so CI can run a fast subset."""
    seen: list[str] = []

    def fake_review(_diff, manifest, *_a, **_k):  # type: ignore[no-untyped-def]
        seen.append(manifest.name)
        return _score(manifest.name, 1, 1)

    monkeypatch.setattr(run_mod, "_review", fake_review)

    run_mod.main(
        ["--provider", "ollama", "--model", "x", "--min-recall", "0.0", "--fixture", "badcode"]
    )
    assert seen == ["badcode"]


def test_no_fixture_flag_runs_every_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --fixture the runner reviews all fixtures, as before."""
    seen: list[str] = []

    def fake_review(_diff, manifest, *_a, **_k):  # type: ignore[no-untyped-def]
        seen.append(manifest.name)
        return _score(manifest.name, 1, 1)

    monkeypatch.setattr(run_mod, "_review", fake_review)

    run_mod.main(["--provider", "ollama", "--model", "x", "--min-recall", "0.0"])
    assert set(seen) == {"badcode", "vibe-multifile", "rlm-bigfile"}


def test_unknown_fixture_name_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo'd --fixture must error, never vacuously pass by running zero fixtures."""

    def fake_review(_diff, manifest, *_a, **_k):  # type: ignore[no-untyped-def]
        return _score(manifest.name, 1, 1)

    monkeypatch.setattr(run_mod, "_review", fake_review)

    with pytest.raises(SystemExit):
        run_mod.main(
            ["--provider", "ollama", "--model", "x", "--min-recall", "0.0", "--fixture", "nope"]
        )


def test_sampling_params_thread_to_build_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """--temperature/--top-p/--top-k reach build_provider so they steer the model.

    They land in the provider's default_opts and litellm forwards them to ollama —
    the recommended non-thinking sampling for qwen3.x (temp 0.6, top_p 0.8, top_k 20).
    """
    calls = _capture_build_provider(monkeypatch)

    run_mod.main(
        [
            "--provider",
            "ollama",
            "--model",
            "x",
            "--min-recall",
            "0.0",
            "--temperature",
            "0.6",
            "--top-p",
            "0.8",
            "--top-k",
            "20",
        ]
    )

    assert calls, "build_provider was never called"
    assert calls[0]["temperature"] == pytest.approx(0.6)
    assert calls[0]["top_p"] == pytest.approx(0.8)
    assert calls[0]["top_k"] == 20


def test_sampling_params_omitted_when_not_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the flags, no sampling kwargs are forced — the model keeps its defaults."""
    calls = _capture_build_provider(monkeypatch)

    run_mod.main(["--provider", "ollama", "--model", "x", "--min-recall", "0.0"])

    assert calls
    assert "temperature" not in calls[0]
    assert "top_p" not in calls[0]
    assert "top_k" not in calls[0]


def test_categories_flag_scopes_review_lenses(monkeypatch: pytest.MonkeyPatch) -> None:
    """--categories cuts the fan-out to a subset so a CI smoke runs fewer model calls."""
    seen: list[list[ReviewCategory]] = []

    real_review = run_mod.LLMReviewEngine.review

    def spy_review(self, ctx, cfg):  # type: ignore[no-untyped-def]
        seen.append(list(cfg.categories))
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
            "--categories",
            "security,correctness",
        ]
    )

    assert seen
    assert all(c == [ReviewCategory.security, ReviewCategory.correctness] for c in seen)


def test_unknown_category_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo'd --categories must error, not silently run a different/empty lens set."""
    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _ShellInjectionProvider())

    with pytest.raises(SystemExit):
        run_mod.main(
            ["--provider", "ollama", "--model", "x", "--min-recall", "0.0", "--categories", "bogus"]
        )


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
