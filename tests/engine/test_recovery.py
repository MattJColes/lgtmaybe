"""The recovery ladder called directly, through its seam (see ``engine/recovery.py``).

The truncation, step-down, escalation and rescue suites drive the ladder
through a whole review; these pin the seam itself: what each rung asks of the
adapter's recovery steps and of the two callbacks into the engine.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from lgtmaybe.core.models import ReviewFinding, ReviewResult, Severity
from lgtmaybe.core.ports import Message, ProviderWallTimeout
from lgtmaybe.engine.lenses import _Lens
from lgtmaybe.engine.recovery import (
    _Ladder,
    _PayloadReason,
    _PiecesReason,
    _rescue,
    _RetryableReason,
)
from lgtmaybe.engine.run import _LensOutcome, _PreparedCall, _Run, _RunState
from tests.conftest import make_cfg
from tests.fakes import FakeProvider, RecoveringFakeProvider

_LENS = _Lens(id="security", user_block="check")
_MESSAGES: list[Message] = [{"role": "user", "content": "diff"}]


def _finding(title: str = "f") -> ReviewFinding:
    return ReviewFinding(path="a.py", line=1, severity=Severity.high, title=title, body="b")


def _run(**overrides: Any) -> _Run:
    fields: dict[str, Any] = {
        "model": "m",
        "response_format": ReviewResult,
        "language": None,
        "project": "",
        "deadline_at": None,
        "budget_at": None,
        "retrieval_budget": None,
        "concurrency": 2,
        "cfg": make_cfg(),
        "provider": FakeProvider(),
        "state": _RunState(),
        "changed_files": ("a.py", "b.py"),
    }
    fields.update(overrides)
    return _Run(**fields)


class _Recorder:
    """Both callbacks of the seam, recording each call and answering *outcome*."""

    def __init__(self, outcome: _LensOutcome = ([], None)) -> None:
        self.outcome = outcome
        self.reissued: list[dict[str, Any]] = []
        self.pieces: list[str] = []

    def reissue(
        self,
        messages: list[Message],
        response_format: type[ReviewResult] | None,
        batch_num: int,
        lens: _Lens,
        **flags: Any,
    ) -> _LensOutcome:
        self.reissued.append({"response_format": response_format, **flags})
        return self.outcome

    def review_piece(self, run: _Run, wrapped: str, *args: Any, **kwargs: Any) -> _LensOutcome:
        self.pieces.append(wrapped)
        return self.outcome

    def ladder(self, recovery: Any = None) -> _Ladder:
        return _Ladder(recovery, self.reissue, self.review_piece)


class _Fallback(RecoveringFakeProvider):
    def escalate_model(self) -> str | None:
        return "fallback-m"


class _StepsDown(RecoveringFakeProvider):
    def lower_reasoning_effort(self) -> dict[str, Any] | None:
        return {"reasoning_effort": "low"}


class TestEscalateModel:
    def test_reissues_once_on_the_fallback_with_no_recovery_of_its_own(self) -> None:
        recorder = _Recorder(([_finding()], None))
        run = _run()
        outcome = recorder.ladder(_Fallback())._escalate_model(
            _MESSAGES, ReviewResult, 1, _LENS, "cut", run
        )
        assert outcome == ([_finding()], None)
        assert recorder.reissued == [
            {
                "response_format": ReviewResult,
                "escalate_to": "fallback-m",
                "recover": False,
                "run": run,
            }
        ]

    def test_without_recovery_steps_nothing_is_sent(self) -> None:
        recorder = _Recorder()
        outcome = recorder.ladder(None)._escalate_model(
            _MESSAGES, ReviewResult, 1, _LENS, "cut", _run()
        )
        assert outcome == ([], "cut")
        assert recorder.reissued == []


def test_a_step_down_that_answers_is_recorded_on_the_run() -> None:
    recorder = _Recorder(([_finding()], None))
    run = _run()
    outcome = recorder.ladder(_StepsDown())._retry_lower_effort(
        _MESSAGES, ReviewResult, 1, _LENS, "thought too long", run
    )
    assert outcome == ([_finding()], None)
    assert recorder.reissued[0]["effort"] == {"reasoning_effort": "low"}
    assert run.state.stepped_down == {"security"}


def test_a_piece_has_nothing_smaller_to_try() -> None:
    recorder = _Recorder()
    outcome = recorder.ladder()._remedy_oversized(
        [_finding("salvaged")],
        "cut",
        None,
        messages=_MESSAGES,
        response_format=ReviewResult,
        batch_num=1,
        lens=_LENS,
        run=_run(),
        on_oversized=None,
        effort=None,
        escalate_to=None,
    )
    assert outcome == ([_finding("salvaged")], "cut")
    assert recorder.reissued == []


def test_the_split_reviews_each_piece_through_the_seam() -> None:
    recorder = _Recorder(([_finding()], None))
    run = _run()
    batch = [("a.py", "patch a"), ("b.py", "patch b")]
    findings, reason = recorder.ladder()._review_split(
        "timed out",
        run=run,
        batch=batch,
        intent_block=None,
        hint_block=None,
        dir_block=None,
        batch_num=3,
        lens=_LENS,
    )
    assert reason is None
    assert findings == [_finding(), _finding()]
    assert len(recorder.pieces) == 2
    assert "patch a" in recorder.pieces[0] and "patch b" in recorder.pieces[1]
    assert run.state.split_batches == {3}


def test_a_piece_that_faltered_on_the_provider_hands_the_rescue_its_retry() -> None:
    class _Flaky(_Recorder):
        def review_piece(self, run: _Run, wrapped: str, *a: Any, **k: Any) -> _LensOutcome:
            self.pieces.append(wrapped)
            if "patch b" in wrapped and len(self.pieces) == 2:
                return [], _RetryableReason("RateLimitError: slow down")
            return [_finding(wrapped[-10:])], None

    recorder = _Flaky()
    findings, reason = recorder.ladder()._review_split(
        "timed out",
        run=_run(),
        batch=[("a.py", "patch a"), ("b.py", "patch b")],
        intent_block=None,
        hint_block=None,
        dir_block=None,
        batch_num=1,
        lens=_LENS,
    )
    assert len(findings) == 1
    assert isinstance(reason, _PiecesReason)
    retried, retry_reason = reason.retry()
    assert retry_reason is None
    assert len(retried) == 1
    assert len(recorder.pieces) == 3  # only the faltered piece went again


def test_a_wall_timeout_goes_to_the_split() -> None:
    recorder = _Recorder()
    seen: list[tuple[str, bool]] = []

    def on_oversized(reason: str, *, slice_hunks: bool = False) -> _LensOutcome:
        seen.append((reason, slice_hunks))
        return [_finding()], None

    outcome = recorder.ladder()._call_failed(
        ProviderWallTimeout("the call outlived its budget"),
        0.0,
        messages=_MESSAGES,
        response_format=ReviewResult,
        batch_num=1,
        lens=_LENS,
        run=_run(),
        on_oversized=on_oversized,
        effort=None,
        escalate_to=None,
    )
    assert outcome == ([_finding()], None)
    [(reason, slice_hunks)] = seen
    assert isinstance(reason, _PayloadReason)
    assert slice_hunks is False


def test_a_provider_failure_is_reported_as_retryable() -> None:
    findings, reason = (
        _Recorder()
        .ladder()
        ._call_failed(
            ConnectionError("reset by peer"),
            0.0,
            messages=_MESSAGES,
            response_format=ReviewResult,
            batch_num=1,
            lens=_LENS,
            run=_run(),
            on_oversized=None,
            effort=None,
            escalate_to=None,
        )
    )
    assert findings == []
    assert isinstance(reason, _RetryableReason)
    assert reason == "ConnectionError: reset by peer"


class TestRescue:
    @staticmethod
    def _call(outcome: _LensOutcome, ran: list[str], lens_id: str) -> _PreparedCall:
        def task() -> _LensOutcome:
            ran.append(lens_id)
            return outcome

        return _PreparedCall(lens_id=lens_id, task=partial(task))

    def test_only_provider_side_failures_run_again(self) -> None:
        ran: list[str] = []
        calls = [
            self._call(([_finding("again")], None), ran, "security"),
            self._call(([], None), ran, "docs"),
        ]
        outcomes: list[_LensOutcome] = [
            ([_finding("first")], _RetryableReason("RateLimitError")),
            ([], "unparseable model output (prose)"),
        ]
        merged = _rescue(calls, outcomes)
        assert ran == ["security"]
        assert merged[0] == ([_finding("first"), _finding("again")], None)
        assert merged[1] == outcomes[1]

    def test_nothing_to_rescue_returns_the_outcomes(self) -> None:
        outcomes: list[_LensOutcome] = [([_finding()], None)]
        assert _rescue([], outcomes) is outcomes
