"""Per-review state lives on the run, never on the engine instance.

One `LLMReviewEngine` may serve two reviews at once (a long-lived process, a
test harness, an eval sweep). Each review's summary notices and its
`max_review_tokens` budget must describe that review alone, so nothing a
review writes may land on `self` or on the process-wide profiler.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from lgtmaybe.core.models import (
    PRContext,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
    ReviewFinding,
    Severity,
    stamp_degradation,
)
from lgtmaybe.core.ports import Message, ProviderTruncated
from lgtmaybe.engine import LLMReviewEngine, ReviewIncompleteError
from lgtmaybe.engine.notices import _SCHEMA_DROP_NOTE, _TEMPERATURE_SEGMENT
from lgtmaybe.engine.profiling import profiler
from tests.conftest import make_cfg

_ALPHA = PRContext(
    diff="@@ -1,3 +1,4 @@\n context\n+alpha change\n context\n",
    changed_files=["a.py"],
    base_sha="abc",
    head_sha="def",
    repo="org/repo",
    pr_number=1,
)
_BETA = _ALPHA.model_copy(
    update={"diff": "@@ -1,3 +1,4 @@\n context\n+beta change\n context\n", "pr_number": 2}
)

# Alpha's every call is expensive and floods past a cap of one finding; beta's
# is cheap and returns a single finding. Two reviews that shared state would
# leak alpha's flood notice and alpha's spend into beta's summary.
_ALPHA_COST = 10_000
_BETA_COST = 10


def _findings_json(count: int) -> str:
    return json.dumps(
        [
            ReviewFinding(
                path="a.py",
                line=line,
                severity=Severity.medium,
                title=f"issue {line}",
                body="broken",
                failure_scenario="When the changed line runs, the operation fails.",
            ).model_dump(mode="json")
            for line in range(1, count + 1)
        ]
    )


def _user_text(messages: list[Message]) -> str:
    return "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")


def _cfg(**overrides: object) -> ReviewConfig:
    base: dict[str, object] = {
        # Serial, so the second lens call is decided after the first one's
        # tokens have landed.
        "max_concurrency": 1,
        "categories": [ReviewCategory.security, ReviewCategory.correctness],
    }
    base.update(overrides)
    return make_cfg(**base)


class _InterleavingProvider:
    """Holds beta's first call open until alpha's whole review has finished.

    So alpha runs start to finish while beta is mid-fan-out: alpha's prepare,
    calls and summary all land between beta's prepare and beta's summary,
    which is exactly the window shared state would leak through.
    """

    def __init__(self) -> None:
        self.beta_in_call = threading.Event()
        self.alpha_done = threading.Event()
        self._lock = threading.Lock()
        self.calls: dict[str, int] = {"alpha": 0, "beta": 0}

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        review = "alpha" if "alpha change" in _user_text(messages) else "beta"
        with self._lock:
            self.calls[review] += 1
            first = self.calls[review] == 1
        if review == "alpha":
            return ProviderResult(text=_findings_json(3), input_tokens=_ALPHA_COST, output_tokens=0)
        if first:
            self.beta_in_call.set()
            assert self.alpha_done.wait(timeout=30), "alpha never finished"
        return ProviderResult(text=_findings_json(1), input_tokens=_BETA_COST, output_tokens=0)


class TestConcurrentReviewsOnOneEngine:
    def test_two_reviews_keep_independent_notices_and_budgets(self) -> None:
        provider = _InterleavingProvider()
        engine = LLMReviewEngine(provider)
        outcome: dict[str, tuple[list[ReviewFinding], str] | BaseException] = {}

        def run_beta() -> None:
            try:
                # Uncapped, and a budget beta's own spend never reaches.
                outcome["beta"] = engine.review(
                    _BETA, _cfg(max_findings_per_lens=0, max_review_tokens=1_000)
                )
            except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
                outcome["beta"] = exc

        beta = threading.Thread(target=run_beta)
        beta.start()
        assert provider.beta_in_call.wait(timeout=30), "beta never reached its first call"
        try:
            # Capped at one finding, and a budget its first call alone blows.
            _, alpha_summary = engine.review(
                _ALPHA, _cfg(max_findings_per_lens=1, max_review_tokens=5_000)
            )
        finally:
            provider.alpha_done.set()
            beta.join(timeout=30)

        result = outcome["beta"]
        if isinstance(result, BaseException):
            raise result
        _, beta_summary = result

        # Alpha's own outcome: flooded past its cap, then stopped by its budget.
        assert "Bounded a lens to the top 1 findings" in alpha_summary
        assert "max_review_tokens" in alpha_summary
        assert provider.calls["alpha"] == 1
        # Beta saw none of it: its cap stayed off, its budget counted only its
        # own two cheap calls, and both of them ran.
        assert "Bounded a lens" not in beta_summary
        assert "max_review_tokens" not in beta_summary
        assert provider.calls["beta"] == 2


class _DegradingForAlpha:
    """Reports alpha's calls degraded and beta's intact, on ONE shared provider.

    Alpha's first call answers with the temperature withheld; every later alpha
    call fails having lost the schema, stamped on the exception the way the
    litellm adapter stamps a failed call. Beta's first call is held open until
    alpha's whole review has finished, so a drop recorded anywhere shared (the
    adapter, the engine) would be read back into beta's summary.
    """

    def __init__(self) -> None:
        self.beta_in_call = threading.Event()
        self.alpha_done = threading.Event()
        self._lock = threading.Lock()
        self.calls: dict[str, int] = {"alpha": 0, "beta": 0}

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        review = "alpha" if "alpha change" in _user_text(messages) else "beta"
        with self._lock:
            self.calls[review] += 1
            first = self.calls[review] == 1
        if review == "alpha":
            if first:
                return ProviderResult(
                    text=_findings_json(1),
                    input_tokens=1,
                    output_tokens=1,
                    temperature_dropped=True,
                )
            exc = RuntimeError("upstream exploded")
            stamp_degradation(exc, schema_dropped=True, temperature_dropped=False)
            raise exc
        if first:
            self.beta_in_call.set()
            assert self.alpha_done.wait(timeout=30), "alpha never finished"
        return ProviderResult(text=_findings_json(1), input_tokens=1, output_tokens=1)


class TestConcurrentReviewsReportTheirOwnDegradation:
    def test_schema_and_temperature_drops_stay_with_the_review_that_had_them(self) -> None:
        """Two reviews on one engine and one provider: each summary discloses
        only the drops its own calls reported."""
        provider = _DegradingForAlpha()
        engine = LLMReviewEngine(provider)
        outcome: dict[str, tuple[list[ReviewFinding], str] | BaseException] = {}

        def run_beta() -> None:
            try:
                outcome["beta"] = engine.review(_BETA, _cfg())
            except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
                outcome["beta"] = exc

        beta = threading.Thread(target=run_beta)
        beta.start()
        assert provider.beta_in_call.wait(timeout=30), "beta never reached its first call"
        try:
            _, alpha_summary = engine.review(_ALPHA, _cfg())
        finally:
            provider.alpha_done.set()
            beta.join(timeout=30)

        result = outcome["beta"]
        if isinstance(result, BaseException):
            raise result
        _, beta_summary = result

        assert _SCHEMA_DROP_NOTE in alpha_summary
        assert _TEMPERATURE_SEGMENT in alpha_summary
        assert _SCHEMA_DROP_NOTE not in beta_summary
        assert _TEMPERATURE_SEGMENT not in beta_summary


class TestEngineKeepsNoRunState:
    def test_a_review_leaves_the_engine_attributes_untouched(self) -> None:
        """Only constructor-injected collaborators live on the instance."""

        class _Provider:
            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                return ProviderResult(text=_findings_json(3), input_tokens=1, output_tokens=1)

        engine = LLMReviewEngine(_Provider())
        before = dict(vars(engine))
        engine.review(_ALPHA, _cfg(max_findings_per_lens=1, max_review_tokens=1_000))
        assert vars(engine) == before


class TestBudgetIgnoresTheProfiler:
    def test_enforcement_never_reads_the_profiler_total(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The profiler is process-wide: reading it would charge this review
        for every other review running in the process."""

        def _forbidden() -> int:
            raise AssertionError("max_review_tokens must not read the profiler")

        monkeypatch.setattr(profiler, "total_tokens", _forbidden)

        class _Costly:
            calls = 0

            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                type(self).calls += 1
                return ProviderResult(text=_findings_json(1), input_tokens=600, output_tokens=400)

        _, summary = LLMReviewEngine(_Costly()).review(_ALPHA, _cfg(max_review_tokens=1_000))
        assert _Costly.calls == 1, "the second call is past the budget"
        assert "max_review_tokens" in summary


class TestEveryModelCallIsCharged:
    """The run tally must see every model call the profiler records during a
    review, not only the lens calls, or the budget under-counts."""

    def test_the_triage_call_counts_against_the_budget(self) -> None:
        calls: list[str] = []

        class _CostlyTriage:
            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                if model == "triage-m":
                    calls.append("triage")
                    verdict = {"files": [{"path": "a.py", "review": True, "risk": 5}]}
                    return ProviderResult(
                        text=json.dumps(verdict), input_tokens=10_000, output_tokens=0
                    )
                calls.append("lens")
                return ProviderResult(text=_findings_json(1), input_tokens=1, output_tokens=1)

        with pytest.raises(ReviewIncompleteError):
            LLMReviewEngine(_CostlyTriage()).review(
                _ALPHA, _cfg(triage_model="triage-m", max_review_tokens=1_000)
            )
        assert calls == ["triage"], "triage spent the budget, so no lens call ran"

    def test_the_repair_call_counts_against_the_budget(self) -> None:
        calls: list[str] = []

        class _CostlyRepair:
            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                system = str(messages[0].get("content", ""))
                if "convert a code reviewer's answer" in system:
                    calls.append("repair")
                    return ProviderResult(
                        text=_findings_json(1), input_tokens=10_000, output_tokens=0
                    )
                calls.append("lens")
                return ProviderResult(text="Looks fine to me.", input_tokens=1, output_tokens=1)

        _, summary = LLMReviewEngine(_CostlyRepair()).review(
            _ALPHA, _cfg(max_review_tokens=1_000, structured_output=False)
        )
        assert calls == ["lens", "repair"], "the repair spent the budget for the second lens"
        assert "max_review_tokens" in summary

    def test_a_truncated_call_is_charged_what_it_spent(self) -> None:
        """A truncation reached the model and ran to its ceiling: usually the
        costliest call of the run, so the budget must see it, not a zero."""
        calls: list[str] = []

        class _Runaway:
            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                calls.append("lens")
                raise ProviderTruncated(
                    "ceiling", text="", input_tokens=1_000, output_tokens=32_768
                )

        with pytest.raises(ReviewIncompleteError):
            LLMReviewEngine(_Runaway()).review(_ALPHA, _cfg(max_review_tokens=10_000))
        assert calls == ["lens"], "the runaway spent the budget, so the second lens never ran"

    def test_the_reflection_call_is_charged_to_the_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reflection runs after the fan-out, so no skip can show its spend:
        read the run's own tally instead. The audit goes through the run's
        metered provider like every other call, so the tally holds the lens
        call's tokens and the audit's."""
        from lgtmaybe.engine import engine as engine_module

        states: list[engine_module._RunState] = []

        class _SpiedState(engine_module._RunState):
            def __init__(self, **fields: Any) -> None:
                super().__init__(**fields)
                states.append(self)

        monkeypatch.setattr(engine_module, "_RunState", _SpiedState)

        class _CostlyAudit:
            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                if model == "reflect-m":
                    # Unparseable on purpose: reflection then keeps everything.
                    return ProviderResult(text="no verdict", input_tokens=7_000, output_tokens=0)
                return ProviderResult(text=_findings_json(1), input_tokens=30, output_tokens=12)

        LLMReviewEngine(_CostlyAudit()).review(
            _ALPHA,
            _cfg(
                categories=[ReviewCategory.security],
                reflect=True,
                reflect_model="reflect-m",
                max_review_tokens=1_000_000,
            ),
        )
        assert len(states) == 1
        assert states[0].tokens_spent() == 30 + 12 + 7_000


class TestAFailedCallStillReportsItsDegradation:
    def test_a_temperature_lost_by_a_failed_call_reaches_the_summary(self) -> None:
        """A failure has no result to carry the drop home on, so it rides the
        exception, and the run's metered provider must read it from there."""

        class _FailsWithoutTheTemperature:
            calls = 0

            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                type(self).calls += 1
                if type(self).calls == 1:
                    return ProviderResult(text=_findings_json(1), input_tokens=1, output_tokens=1)
                exc = RuntimeError("upstream exploded")
                stamp_degradation(exc, schema_dropped=False, temperature_dropped=True)
                raise exc

        _, summary = LLMReviewEngine(_FailsWithoutTheTemperature()).review(_ALPHA, _cfg())

        assert "results may be incomplete" in summary, "the second lens did fail"
        assert _TEMPERATURE_SEGMENT in summary
