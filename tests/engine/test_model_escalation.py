"""Tests for the last rung of the truncation ladder — escalating to the fallback model.

A truncation has two remedies that run on the model the user chose: shrink the
payload (``_review_split``) when the answer outgrew the ceiling, or lower the
thinking budget (``_retry_lower_effort``) when the *reasoning* did. Both are
cheap, and both aim at the cause the token counts actually named.

Switching model is the third, and it is last on purpose. It re-sends the same
request at the same ceiling to a second model — the expensive guess, and the one
that reaches for a bigger model to solve a problem the counts already explained.
Before this, the adapter took it FIRST: its own fallback fired the moment the
primary truncated, so the two aimed remedies never ran at all.

The failure this is measured against: a lens truncating after 32,768 output
tokens against 6,281 input, on a diff of a few hundred lines. Nothing large was
being written, so nothing about the payload size predicted it — which is why the
remedy has to be chosen from the counts and not from the diff.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from lgtmaybe.core.models import PRContext, ProviderResult, ReviewCategory, ReviewConfig
from lgtmaybe.core.ports import Message, ProviderTruncated
from lgtmaybe.engine.engine import LLMReviewEngine, ReviewIncompleteError
from tests.conftest import make_cfg
from tests.fakes import FakeProvider

_CTX = PRContext(
    diff=(
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,2 +1,3 @@\n"
        " import sqlite3\n"
        '+query = "select * from t where id = " + user_id\n'
        " done = True\n"
    ),
    changed_files=["src/app.py"],
    base_sha="abc",
    head_sha="def",
    repo="o/r",
    pr_number=1,
)

_FINDINGS_JSON = json.dumps(
    {
        "findings": [
            {
                "path": "src/app.py",
                "line": 2,
                "side": "RIGHT",
                "severity": "high",
                "title": "SQL injection",
                "body": "user_id is interpolated into the query",
                "anchor": 'query = "select * from t where id = " + user_id',
                "failure_scenario": "an attacker passes a quote in user_id",
            }
        ]
    }
)

PRIMARY = "luna"
FALLBACK = "sonnet"


def _cfg(**overrides: object) -> ReviewConfig:
    """One lens, run serially, so the call log reads as a sequence, not a race."""
    base: dict[str, object] = {
        "categories": [ReviewCategory.security],
        "max_concurrency": 1,
        "model": PRIMARY,
    }
    base.update(overrides)
    return make_cfg(**base)


def _reasoning_bound() -> ProviderTruncated:
    """The whole ceiling went on thinking — the shape a split cannot help."""
    return ProviderTruncated(
        "response hit the 32768-token `max_tokens` ceiling",
        text="",
        reasoning_tokens=32100,
        output_tokens=32768,
        input_tokens=6281,
    )


def _payload_bound() -> ProviderTruncated:
    """Little thought, then an answer that ran long — the split's own shape."""
    return ProviderTruncated(
        "response hit the 32768-token `max_tokens` ceiling",
        text="",
        reasoning_tokens=120,
        output_tokens=32768,
        input_tokens=6281,
    )


class _Escalating(FakeProvider):
    """Truncates on the primary however it is asked; answers on the fallback.

    Stands for the real case: a model whose generation runs away on this batch,
    where a second model simply does not. Every remedy aimed at the primary
    fails, so the ladder has to reach its last rung to produce a review at all.
    """

    def __init__(self, exc_factory: Any = _reasoning_bound) -> None:
        super().__init__()
        self._exc_factory = exc_factory
        self.fallback_model = FALLBACK
        self.model = PRIMARY

    def escalate_model(self) -> str | None:
        return self.fallback_model

    def lower_reasoning_effort(self) -> dict[str, Any] | None:
        return {"reasoning_effort": "low"}

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        if opts.get("model_override") == FALLBACK:
            return ProviderResult(
                text=_FINDINGS_JSON, input_tokens=50, output_tokens=20, model=FALLBACK
            )
        raise self._exc_factory()


class _NoFallback(_Escalating):
    """The same failure with nothing to escalate to — today's behaviour, exactly."""

    def escalate_model(self) -> str | None:
        return None


def _lens_calls(provider: FakeProvider) -> list[dict[str, Any]]:
    return [c for c in provider.calls if "wrapped" not in str(c.get("model"))]


# ---------------------------------------------------------------------------
# ordering — the point of the change
# ---------------------------------------------------------------------------


def test_a_reasoning_bound_truncation_steps_down_before_it_escalates() -> None:
    """The cheap, aimed remedy runs on the primary first. Escalating straight to
    a second model is what this replaces."""
    provider = _Escalating()

    LLMReviewEngine(provider).review(_CTX, _cfg())

    overrides = [c["opts"].get("model_override") for c in provider.calls]
    efforts = [c["opts"].get("reasoning_effort") for c in provider.calls]
    # First the primary, then the step-down on the primary, then the fallback.
    assert overrides == [None, None, FALLBACK]
    assert efforts == [None, "low", None]


def test_a_payload_bound_truncation_splits_before_it_escalates() -> None:
    """The other diagnosis, the other rung — same ordering rule."""
    provider = _Escalating(exc_factory=_payload_bound)

    LLMReviewEngine(provider).review(_CTX, _cfg())

    overrides = [c["opts"].get("model_override") for c in provider.calls]
    assert overrides[0] is None
    assert overrides[-1] == FALLBACK
    assert overrides.count(FALLBACK) == 1


def test_the_escalation_produces_the_review() -> None:
    findings, summary = LLMReviewEngine(_Escalating()).review(_CTX, _cfg())

    assert [f.title for f in findings] == ["SQL injection"]
    assert "results may be incomplete" not in summary


def test_lens_calls_defer_their_truncation_to_the_engine() -> None:
    """The adapter must not spend its own fallback call first — the engine owns
    this ordering, and it can only own it if the failure reaches it."""
    provider = _Escalating()

    LLMReviewEngine(provider).review(_CTX, _cfg())

    assert provider.calls[0]["opts"].get("defer_truncation") is True


# ---------------------------------------------------------------------------
# bounds
# ---------------------------------------------------------------------------


def test_the_escalation_is_attempted_once() -> None:
    """One second model, not a walk down a roster. A fallback that truncates too
    reports the failure rather than spending the review proving it."""

    class _BothTruncate(_Escalating):
        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            self.calls.append({"messages": messages, "model": model, "opts": opts})
            raise self._exc_factory()

    provider = _BothTruncate()
    # The lone lens produced nothing, so the review has nothing to post — the
    # failure the engine raises rather than a summary claiming a clean bill.
    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(_CTX, _cfg())

    overrides = [c["opts"].get("model_override") for c in provider.calls]
    assert overrides.count(FALLBACK) == 1


def test_the_escalation_starts_no_ladder_of_its_own() -> None:
    """It is the LAST rung, so it takes no remedy for its own failure. Letting it
    step down would re-run the primary at an effort the primary already failed
    at — a fourth call, and one already proven not to answer."""

    class _BothTruncate(_Escalating):
        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            self.calls.append({"messages": messages, "model": model, "opts": opts})
            raise self._exc_factory()

    provider = _BothTruncate()
    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(_CTX, _cfg())

    spent = [
        (c["opts"].get("model_override"), c["opts"].get("reasoning_effort")) for c in provider.calls
    ]
    # Primary, primary stepped down, fallback. Nothing after the fallback.
    assert spent == [(None, None), (None, "low"), (FALLBACK, None)]


def test_no_fallback_configured_changes_nothing() -> None:
    """A run without a second model must send byte-identical requests and pay
    exactly what it paid before."""
    provider = _NoFallback()

    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(_CTX, _cfg())

    assert all(c["opts"].get("model_override") is None for c in provider.calls)


def test_a_spent_token_budget_stops_the_escalation() -> None:
    """The escalation is a new model call, so every whole-review ceiling applies
    to it — the same re-check the step-down beside it makes. A truncation is
    routinely the most expensive call in a run and is charged for, so the very
    failure that reaches for the fallback can be the one that spends the budget."""
    provider = _Escalating()

    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(_CTX, _cfg(max_review_tokens=1))

    assert all(c["opts"].get("model_override") is None for c in provider.calls)


# ---------------------------------------------------------------------------
# disclosure
# ---------------------------------------------------------------------------


def test_the_escalation_is_named_in_its_own_notice() -> None:
    """The findings are complete, so this is not the incomplete notice — but a
    lens that keeps needing the second model is telling the reader to make it the
    first one, and that is unreadable if the rescue is silent."""
    _findings, summary = LLMReviewEngine(_Escalating()).review(_CTX, _cfg())

    assert FALLBACK in summary
    assert "security" in summary


def test_the_adapters_own_fallback_is_disclosed_too() -> None:
    """Not every rescue is the engine's. The adapter still switches model on a
    5xx or a bad gateway, and the result names which model answered — so the
    notice fires on that path as well, without the engine being told."""

    class _AdapterRescued(FakeProvider):
        model = PRIMARY

        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            self.calls.append({"messages": messages, "model": model, "opts": opts})
            return ProviderResult(
                text=_FINDINGS_JSON, input_tokens=50, output_tokens=20, model=FALLBACK
            )

    findings, summary = LLMReviewEngine(_AdapterRescued()).review(_CTX, _cfg())

    assert [f.title for f in findings] == ["SQL injection"]
    assert FALLBACK in summary
