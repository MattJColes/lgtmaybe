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


def test_gate_fails_on_false_positive() -> None:
    """A forbidden (cross-file) finding firing fails the run even at full recall —
    the humility regression signal the cross-file-fp fixture exists to catch."""
    fp = FixtureScore(
        name="cross-file-fp",
        parsed_ok=True,
        expected_count=1,
        matched_count=1,
        findings_count=2,
        missed=[],
        false_positives=["FP: model_dump may pass fields absent from V2"],
    )
    ok, _ = run_mod._gate([fp], 0.0)
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


def test_pooled_precision_reported_in_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The summary reports a pooled precision — sum of wrong / sum of adjudicable,
    not an average of per-fixture percentages."""
    # fixture A: 2 adjudicable, 0 wrong (precision 100%).
    # fixture B: 2 adjudicable, 2 wrong (precision 0%).
    # Averaging percentages → 50%. Pooling over counts → (0+2)/(2+2) wrong = 50% wrong
    # → 50% precision here too, so make the split uneven to tell them apart:
    # A: 4 adjudicable / 0 wrong, B: 1 adjudicable / 1 wrong.
    # avg of pct = (100% + 0%)/2 = 50%; pooled = 1 wrong / 5 adjudicable = 80% precision.
    scores = [
        FixtureScore(
            name="a",
            parsed_ok=True,
            expected_count=4,
            matched_count=4,
            findings_count=4,
            missed=[],
            adjudicable_count=4,
            forbidden_count=0,
            unexpected_count=0,
        ),
        FixtureScore(
            name="b",
            parsed_ok=True,
            expected_count=1,
            matched_count=1,
            findings_count=2,
            missed=[],
            adjudicable_count=1,
            forbidden_count=1,
            unexpected_count=0,
        ),
    ]

    def fake_review(_diff, manifest, *_a, **_k):  # type: ignore[no-untyped-def]
        return scores.pop(0)

    monkeypatch.setattr(run_mod, "_review", fake_review)
    run_mod.main(
        [
            "--provider",
            "ollama",
            "--model",
            "x",
            "--min-recall",
            "0.0",
            "--fixture",
            "badcode",
            "--fixture",
            "vibe-multifile",
        ]
    )
    out = capsys.readouterr().out
    # pooled precision = 1 - 1/5 = 80%, NOT the 50% an average-of-percentages gives.
    assert "precision 80%" in out
    assert "precision 50%" not in out


def test_per_fixture_precision_printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each fixture line shows its own precision."""
    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _ShellInjectionProvider())
    run_mod.main(
        ["--provider", "ollama", "--model", "x", "--min-recall", "0.0", "--fixture", "badcode"]
    )
    out = capsys.readouterr().out
    assert "precision=" in out


def _run_json(capsys: pytest.CaptureFixture[str], extra: list[str]) -> dict[str, object]:
    """Run the harness with --json and return the parsed payload."""
    rc = run_mod.main(
        ["--provider", "ollama", "--model", "m", "--min-recall", "0.0", "--json", *extra]
    )
    assert rc in (0, 1)
    return json.loads(capsys.readouterr().out)  # type: ignore[no-any-return]


def test_json_flag_emits_machine_readable_scores(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json prints one JSON object with the per-fixture scores + pooled metrics —
    the shape the evals.ab A/B harness parses from a worktree subprocess."""
    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _ShellInjectionProvider())
    rc = run_mod.main(
        [
            "--provider",
            "ollama",
            "--model",
            "x",
            "--min-recall",
            "0.0",
            "--fixture",
            "badcode",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "fixtures" in payload
    assert "pooled_recall" in payload
    assert "pooled_precision" in payload
    assert "pooled_anchored" in payload
    assert payload["fixtures"][0]["name"] == "badcode"


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
        [
            "--provider",
            "openai",
            "--model",
            "x",
            "--api-key",
            "sk-test",
            "--min-recall",
            "0.0",
            "--num-ctx",
            "9000",
        ]
    )

    assert calls
    assert "num_ctx" not in calls[0]


def test_keyless_openai_compatible_gets_placeholder_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A keyless local endpoint (LM Studio / llama.cpp / vLLM) must still reach
    build_provider with a key — the OpenAI client litellm uses rejects an empty
    one — so the harness resolves credentials like the CLI does."""
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    calls = _capture_build_provider(monkeypatch)

    run_mod.main(
        [
            "--provider",
            "openai-compatible",
            "--model",
            "qwen",
            "--api-base",
            "http://localhost:1234/v1",
            "--min-recall",
            "0.0",
        ]
    )

    assert calls
    assert calls[0]["api_key"]  # a placeholder, not None/empty
    assert calls[0]["api_base"] == "http://localhost:1234/v1"


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
    assert {
        "badcode",
        "vibe-multifile",
        "rlm-bigfile",
        "rlm-pipeline",
        "cross-file-fp",
    } <= set(seen)


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


def _capture_cfg(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> list:
    """Run main with *argv* and return the ReviewConfig objects the engine saw."""
    seen: list = []
    real_review = run_mod.LLMReviewEngine.review

    def spy_review(self, ctx, cfg):  # type: ignore[no-untyped-def]
        seen.append(cfg)
        return real_review(self, ctx, cfg)

    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _ShellInjectionProvider())
    monkeypatch.setattr(run_mod.LLMReviewEngine, "review", spy_review)
    run_mod.main(argv)
    return seen


_BASE_ARGV = ["--provider", "ollama", "--model", "x", "--min-recall", "0.0"]


def test_review_deadline_is_disabled_for_evals(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production max_review_seconds ceiling must never apply in an eval:
    on a slow local model it would skip calls mid-fixture and silently melt the
    recall the run exists to measure."""
    seen = _capture_cfg(monkeypatch, [*_BASE_ARGV, "--fixture", "badcode"])
    assert seen and all(cfg.max_review_seconds == 0 for cfg in seen)


def test_preset_flag_threads_to_review_config(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_cfg(monkeypatch, [*_BASE_ARGV, "--fixture", "badcode", "--preset", "full"])
    assert seen and all(cfg.preset.value == "full" for cfg in seen)


def test_preset_defaults_to_the_production_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --preset the eval measures what production ships: fast."""
    seen = _capture_cfg(monkeypatch, [*_BASE_ARGV, "--fixture", "badcode"])
    assert seen and all(cfg.preset.value == "fast" for cfg in seen)


# ---------------------------------------------------------------------------
# per-call failure detail
#
# `parsed_ok` is a single boolean that only goes False when EVERY call failed
# and nothing came back, and it conflates a parse failure with a timeout, a bad
# key and a spent quota. A run where three of four lenses returned prose scores
# `parsed_ok=True` with merely depressed recall — which is exactly how an
# intermittent schema-compliance problem hides. The per-call reasons the engine
# already records are the fix.
# ---------------------------------------------------------------------------


def test_failed_lenses_are_reported_per_fixture(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Prose(FakeProvider):
        def complete(self, messages, model, **opts):  # type: ignore[override]
            self.calls.append({"messages": messages, "model": model, "opts": opts})
            return ProviderResult(text="no issues here", input_tokens=7, output_tokens=3)

    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _Prose())
    payload = _run_json(capsys, ["--fixture", "badcode"])

    failures = payload["fixtures"][0]["lens_failures"]
    assert failures, "a wholly unparseable run must name what failed"
    assert all("unparseable model output (prose)" in f for f in failures)


def test_a_clean_fixture_reports_no_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _ShellInjectionProvider())
    payload = _run_json(capsys, ["--fixture", "badcode"])
    assert payload["fixtures"][0]["lens_failures"] == []


def test_token_totals_are_per_fixture_not_cumulative(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The profiler is a process-wide singleton the harness never reset, so a
    multi-fixture run silently accumulated every earlier fixture's counts."""
    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _ShellInjectionProvider())
    payload = _run_json(capsys, ["--fixture", "badcode", "--fixture", "lazy-imports"])

    first, second = payload["fixtures"]
    assert first["output_tokens"] > 0
    assert second["output_tokens"] > 0
    # Equality, not an inequality: identical work on both fixtures must report
    # identical totals. A cumulative profiler would make the second strictly
    # larger, which is the only thing this can actually detect.
    assert second["output_tokens"] == first["output_tokens"]
    assert second["input_tokens"] == first["input_tokens"], (
        "identical work on both fixtures must report identical, non-accumulating totals"
    )


def test_the_json_payload_names_the_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without it a cross-model benchmark's own output cannot say which model
    produced which row."""
    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _ShellInjectionProvider())
    payload = _run_json(capsys, ["--fixture", "badcode"])
    assert payload["model"] == "m"
    assert payload["provider"] == "ollama"


def test_a_failed_call_is_printed_before_the_misses_it_explains(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Prose(FakeProvider):
        def complete(self, messages, model, **opts):  # type: ignore[override]
            self.calls.append({"messages": messages, "model": model, "opts": opts})
            return ProviderResult(text="no issues here", input_tokens=7, output_tokens=3)

    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _Prose())
    run_mod.main(
        ["--provider", "ollama", "--model", "m", "--min-recall", "0.0", "--fixture", "badcode"]
    )
    out = capsys.readouterr().out
    assert "CALL FAILED" in out
    assert "(prose)" in out, "the operator reads this line, so it names the shape"


# ---------------------------------------------------------------------------
# repeats
#
# Without a repeat axis a fixture either passed or failed, and nothing separated
# "this model can't do it" from "this model does it four times in five". That
# distinction is what the structured-output audit turned on: one model failed a
# case consistently across three repeats (a reproducible defect), another failed
# intermittently. Neither was expressible here.
# ---------------------------------------------------------------------------


def test_repeats_run_each_fixture_more_than_once(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_review(_diff, manifest, *_a, **_k):  # type: ignore[no-untyped-def]
        seen.append(manifest.name)
        return FixtureScore(
            name=manifest.name,
            parsed_ok=True,
            expected_count=1,
            matched_count=1,
            findings_count=1,
            missed=[],
        )

    monkeypatch.setattr(run_mod, "_review", fake_review)
    run_mod.main(
        [
            "--provider",
            "ollama",
            "--model",
            "m",
            "--min-recall",
            "0.0",
            "--fixture",
            "badcode",
            "--repeats",
            "3",
        ]
    )

    assert seen == ["badcode", "badcode", "badcode"]


def test_one_repeat_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing invocations must cost exactly what they cost before."""
    seen: list[str] = []

    def fake_review(_diff, manifest, *_a, **_k):  # type: ignore[no-untyped-def]
        seen.append(manifest.name)
        return FixtureScore(
            name=manifest.name,
            parsed_ok=True,
            expected_count=1,
            matched_count=1,
            findings_count=1,
            missed=[],
        )

    monkeypatch.setattr(run_mod, "_review", fake_review)
    run_mod.main(
        ["--provider", "ollama", "--model", "m", "--min-recall", "0.0", "--fixture", "badcode"]
    )

    assert seen == ["badcode"]


def test_the_payload_stays_a_flat_list_so_the_ab_harness_still_parses_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """evals.ab does `FixtureScore.model_validate(f) for f in payload["fixtures"]`
    and pools raw counts. A flat list of N entries per fixture keeps that working
    and pools correctly; nesting would break it, and ab's parsing has no test to
    catch that."""
    monkeypatch.setattr(run_mod, "build_provider", lambda *a, **k: _ShellInjectionProvider())
    payload = _run_json(capsys, ["--fixture", "badcode", "--repeats", "2"])

    assert payload["repeats"] == 2
    assert [f["name"] for f in payload["fixtures"]] == ["badcode", "badcode"]
    assert [FixtureScore.model_validate(f).name for f in payload["fixtures"]]


def test_repeated_runs_report_their_spread(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A single number cannot say whether a model is reliable. The point of
    repeats is the spread, so it has to reach the operator."""
    recalls = iter([1.0, 0.0])

    def fake_review(_diff, manifest, *_a, **_k):  # type: ignore[no-untyped-def]
        matched = int(next(recalls))
        return FixtureScore(
            name=manifest.name,
            parsed_ok=True,
            expected_count=1,
            matched_count=matched,
            findings_count=matched,
            missed=[],
        )

    monkeypatch.setattr(run_mod, "_review", fake_review)
    run_mod.main(
        [
            "--provider",
            "ollama",
            "--model",
            "m",
            "--min-recall",
            "0.0",
            "--fixture",
            "badcode",
            "--repeats",
            "2",
        ]
    )

    out = capsys.readouterr().out
    assert "min" in out and "max" in out
