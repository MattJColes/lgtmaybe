"""Tests for the schema-less lens re-run — the last recovery before a lens is lost.

lgtmaybe already had two structured-output fallbacks: the provider *rejecting*
``response_format`` (a 400), and schema mode decoding to an *empty* response.
A non-empty reply that simply will not parse had neither — the lens was reported
unparseable and thrown away, even though the prompt itself asks for JSON and the
parser is lenient, so the same request without provider-native schema
enforcement may well have worked.

Two Claude models reproduced this through OpenRouter on lgtmaybe 2.1.4: one
failed the same Rust case in all three repeats, five lenses each returning
860–1,201 output tokens and then failing to parse; the other lost every lens in
two observations. Neither rejected ``response_format`` and neither returned
empty, so neither existing fallback could fire.

Ordering is by cost, and it is the whole design: the reformat call
(``repair.py``) sends the reply back with no diff and is an order of magnitude
cheaper, so it runs first and salvages tokens already paid for. This re-runs the
whole lens, diff and all, so it is the fallback for when that failed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from lgtmaybe.core.models import (
    PRContext,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
    ReviewFinding,
    Severity,
)
from lgtmaybe.core.ports import ProviderTruncated
from lgtmaybe.engine.engine import LLMReviewEngine, ReviewIncompleteError, _response_digest
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

_FINDING = {
    "path": "src/app.py",
    "line": 2,
    "side": "RIGHT",
    "severity": "high",
    "title": "SQL injection",
    "body": "user_id is interpolated into the query",
    "anchor": 'query = "select * from t where id = " + user_id',
    "failure_scenario": "an attacker passes a quote in user_id",
}

_FINDINGS_JSON = json.dumps({"findings": [_FINDING]})


def _cfg(**overrides: object) -> ReviewConfig:
    """One lens, run serially, so a call log reads as a sequence and not a race."""
    base: dict[str, object] = {
        "categories": [ReviewCategory.security],
        "max_concurrency": 1,
    }
    base.update(overrides)
    return make_cfg(**base)


def _is_repair(messages: list[dict[str, Any]]) -> bool:
    return "convert a code reviewer" in "\n".join(str(m.get("content", "")) for m in messages)


def _lens_calls(provider: FakeProvider) -> list[dict[str, Any]]:
    return [c for c in provider.calls if not _is_repair(c["messages"])]


def _repair_calls(provider: FakeProvider) -> list[dict[str, Any]]:
    return [c for c in provider.calls if _is_repair(c["messages"])]


class _CompliesOnlyWithoutTheSchema(FakeProvider):
    """Prose under ``response_format``, findings without it. The bug, distilled."""

    def complete(self, messages, model, **opts):  # type: ignore[override]
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        if _is_repair(messages):
            # The cheap salvage is tried first and does not help here — this
            # model's fault is the schema, not the wrapper it chose.
            return ProviderResult(text="still prose, sorry", input_tokens=5, output_tokens=5)
        if opts.get("response_format") is None:
            return ProviderResult(text=_FINDINGS_JSON, input_tokens=50, output_tokens=20)
        return ProviderResult(
            text="I reviewed the diff and the id is interpolated into the query.",
            input_tokens=50,
            output_tokens=20,
        )


class _NeverComplies(FakeProvider):
    """Prose whatever it is sent. The retry must stop, not cascade."""

    def complete(self, messages, model, **opts):  # type: ignore[override]
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        return ProviderResult(text="prose, always prose", input_tokens=50, output_tokens=20)


# ---------------------------------------------------------------------------
# the recovery itself
# ---------------------------------------------------------------------------


def test_a_malformed_reply_is_re_asked_without_the_schema() -> None:
    findings, summary = LLMReviewEngine(_CompliesOnlyWithoutTheSchema()).review(_CTX, _cfg())

    assert [f.title for f in findings] == ["SQL injection"]
    assert "results may be incomplete" not in summary


def test_the_retry_is_the_same_request_minus_the_schema() -> None:
    """Not a new prompt: the theory is that provider-native enforcement broke a
    reply the prompt would otherwise have got right, so only the schema moves."""
    provider = _CompliesOnlyWithoutTheSchema()

    LLMReviewEngine(provider).review(_CTX, _cfg())

    first, retry = _lens_calls(provider)
    assert first["opts"].get("response_format") is not None
    assert "response_format" not in retry["opts"]
    assert retry["messages"] == first["messages"]


def test_the_recovery_is_named_in_its_own_notice() -> None:
    """Complete, not partial — but a model whose schema mode does not work costs
    two wasted calls per lens, and the reader can turn that off."""
    _findings, summary = LLMReviewEngine(_CompliesOnlyWithoutTheSchema()).review(_CTX, _cfg())

    assert "without the schema" in summary
    assert "structured_output" in summary, "name the knob that skips the wasted calls"


# ---------------------------------------------------------------------------
# bounded — one retry, no cascade
# ---------------------------------------------------------------------------


def test_a_failed_retry_reports_and_stops() -> None:
    provider = _NeverComplies()

    with pytest.raises(ReviewIncompleteError) as exc_info:
        LLMReviewEngine(provider).review(_CTX, _cfg())

    assert "prose" in str(exc_info.value)
    # Exactly three calls for the one lens: the schema-mode call, its reformat,
    # and the schema-less re-run. Nothing more — a retry that fails must not
    # reformat again, or every recovery level doubles the one below it.
    assert len(_lens_calls(provider)) == 2
    assert len(_repair_calls(provider)) == 1


def test_a_truncated_retry_keeps_its_completed_findings() -> None:
    class _TruncatesWithoutSchema(_NeverComplies):
        def complete(self, messages, model, **opts):  # type: ignore[override]
            self.calls.append({"messages": messages, "model": model, "opts": opts})
            if not _is_repair(messages) and "response_format" not in opts:
                raise ProviderTruncated("ceiling", text=_FINDINGS_JSON[:-2])
            return ProviderResult(text="prose", input_tokens=50, output_tokens=20)

    findings, summary = LLMReviewEngine(_TruncatesWithoutSchema()).review(_CTX, _cfg())

    assert [finding.title for finding in findings] == ["SQL injection"]
    assert "unparseable" in summary


def test_the_cheap_repair_runs_first() -> None:
    """A reformat sends no diff and is an order of magnitude cheaper. When it
    works, the expensive whole-lens re-run must never happen."""

    class _RepairsCleanly(FakeProvider):
        def complete(self, messages, model, **opts):  # type: ignore[override]
            self.calls.append({"messages": messages, "model": model, "opts": opts})
            if _is_repair(messages):
                return ProviderResult(text=_FINDINGS_JSON, input_tokens=5, output_tokens=5)
            return ProviderResult(text="prose", input_tokens=50, output_tokens=20)

    provider = _RepairsCleanly()
    findings, summary = LLMReviewEngine(provider).review(_CTX, _cfg())

    assert findings
    assert "reformatted by a second call" in summary
    assert len(_lens_calls(provider)) == 1, "no schema-less re-run was needed"


# ---------------------------------------------------------------------------
# what must NOT trigger it
# ---------------------------------------------------------------------------


def test_a_call_that_never_sent_the_schema_is_not_re_asked() -> None:
    """Without provider-native enforcement there is no enforcement to blame, and
    the retry would be the byte-identical request the rescue wave forbids."""
    provider = _NeverComplies()

    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(_CTX, _cfg(structured_output=False))

    assert len(_lens_calls(provider)) == 1
    assert len(_repair_calls(provider)) == 1


def test_it_is_off_when_disabled() -> None:
    provider = _NeverComplies()

    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(_CTX, _cfg(retry_without_schema=False))

    assert len(_lens_calls(provider)) == 1
    assert len(_repair_calls(provider)) == 1


def test_a_model_whose_schema_the_adapter_already_strips_is_not_re_asked() -> None:
    """Passing `response_format` is not the same as sending it.

    The adapter strips the schema for a model that already refused it, and from
    the engine that call looks identical to one made under enforcement. Re-running
    it "without the schema" would re-send the request that just failed, byte for
    byte — the identical retry the rescue wave forbids, at full diff price.
    """

    class _SchemaAlreadyStripped(_NeverComplies):
        def sends_response_format(self, model: str) -> bool:
            return False

    provider = _SchemaAlreadyStripped()
    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(_CTX, _cfg())

    assert len(_lens_calls(provider)) == 1
    assert len(_repair_calls(provider)) == 1


def test_an_adapter_that_cannot_answer_still_gets_the_retry() -> None:
    """Fail-open, like every other adapter probe: an adapter with no opinion is
    assumed to have sent what it was given, so the recovery keeps working rather
    than silently switching itself off."""
    provider = _CompliesOnlyWithoutTheSchema()
    assert not hasattr(provider, "sends_response_format")

    findings, _summary = LLMReviewEngine(provider).review(_CTX, _cfg())

    assert findings


def test_a_ceiling_reached_by_the_repair_stops_the_retry() -> None:
    """The re-run re-sends the whole diff, so it is the most expensive thing on
    this path — a budget the first call and its reformat have already spent must
    not be blown by it."""

    class _ExpensiveRepair(_NeverComplies):
        def complete(self, messages, model, **opts):  # type: ignore[override]
            result = super().complete(messages, model, **opts)
            if _is_repair(messages):
                # Push the running total past the budget mid-recovery.
                return ProviderResult(text=result.text, input_tokens=10_000, output_tokens=10_000)
            return result

    provider = _ExpensiveRepair()
    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(_CTX, _cfg(max_review_tokens=5_000))

    assert len(_repair_calls(provider)) == 1, "the reformat ran and spent the budget"
    assert len(_lens_calls(provider)) == 1, "…so the full-diff re-run never started"


def test_a_truncated_reply_stays_on_the_truncation_path() -> None:
    """Its complete findings are already salvaged and its batch is already
    re-split; the schema is not what cut it off."""

    class _Truncating(FakeProvider):
        def complete(self, messages, model, **opts):  # type: ignore[override]
            self.calls.append({"messages": messages, "model": model, "opts": opts})
            return ProviderResult(
                text='{"findings": [{"path": "src/app.py", "line": 2, "sev',
                input_tokens=50,
                output_tokens=20,
            )

    provider = _Truncating()
    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(_CTX, _cfg())

    assert all(c["opts"].get("response_format") is not None for c in provider.calls), (
        "a cut-off answer is never re-asked without the schema"
    )


# ---------------------------------------------------------------------------
# remembering it
# ---------------------------------------------------------------------------


class _RecordsTheDrop(_CompliesOnlyWithoutTheSchema):
    def __init__(self) -> None:
        super().__init__()
        self.dropped: list[tuple[str, str]] = []

    def drop_response_format(self, model: str, why: str) -> None:
        self.dropped.append((model, why))


def test_a_successful_retry_tells_the_adapter_to_stop_sending_the_schema() -> None:
    provider = _RecordsTheDrop()

    LLMReviewEngine(provider).review(_CTX, _cfg())

    assert provider.dropped, "later calls must not repeat the schema-mode failure"
    assert provider.dropped[0][0] == "m", "keyed by the model the engine called"


def test_a_later_lens_skips_the_schema_after_the_first_one_recovered() -> None:
    """The point of remembering, asserted end to end.

    Recording the `drop_response_format` call only proves the engine asked. What
    the feature promises is that the NEXT lens stops paying the two wasted calls
    — which a wrong model key, or options rebuilt from scratch per call, would
    break while the recording assertion still passed.
    """

    class _AdapterLike(_CompliesOnlyWithoutTheSchema):
        """Mirrors the real adapter: it strips the schema for a marked model."""

        def __init__(self) -> None:
            super().__init__()
            self._dropped: set[str] = set()

        def drop_response_format(self, model: str, why: str) -> None:
            self._dropped.add(model)

        def sends_response_format(self, model: str) -> bool:
            return model not in self._dropped

        def complete(self, messages, model, **opts):  # type: ignore[override]
            if model in self._dropped:
                opts.pop("response_format", None)
            return super().complete(messages, model, **opts)

    provider = _AdapterLike()
    findings, _summary = LLMReviewEngine(provider).review(
        _CTX, _cfg(categories=[ReviewCategory.security, ReviewCategory.correctness])
    )

    assert findings
    lens_calls = _lens_calls(provider)
    # The first lens pays the full recovery: schema call, then the schema-less
    # re-run. Every later call goes out schema-less from the start.
    assert lens_calls[0]["opts"].get("response_format") is not None
    assert all("response_format" not in c["opts"] for c in lens_calls[1:])
    assert len(_repair_calls(provider)) == 1, "only the first lens needed a reformat"


def test_a_failed_retry_remembers_nothing() -> None:
    """One unparseable reply is not proof the schema is at fault, and disabling
    it for the rest of the run is a quality regression on every later call."""

    class _NeverCompliesRecording(_NeverComplies):
        def __init__(self) -> None:
            super().__init__()
            self.dropped: list[tuple[str, str]] = []

        def drop_response_format(self, model: str, why: str) -> None:
            self.dropped.append((model, why))

    provider = _NeverCompliesRecording()
    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(_CTX, _cfg())

    assert provider.dropped == []


def test_a_provider_that_cannot_remember_still_recovers() -> None:
    """Adapter-only, feature-detected like the other two probes: a fake without
    the method simply never remembers, rather than every fake growing one."""
    provider = _CompliesOnlyWithoutTheSchema()
    assert not hasattr(provider, "drop_response_format")

    findings, _summary = LLMReviewEngine(provider).review(_CTX, _cfg())

    assert findings


# ---------------------------------------------------------------------------
# diagnostics — enough to tell two failures apart, no response content
# ---------------------------------------------------------------------------


def test_the_failure_log_identifies_the_response_without_quoting_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The benchmark evidence this came from could not tell a schema-valid but
    wrong envelope from prose, because nothing about the response was retained.
    A digest makes two failures comparable across runs; the body stays out."""
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ReviewIncompleteError):
            LLMReviewEngine(_NeverComplies()).review(_CTX, _cfg())

    unparseable = [r for r in caplog.records if "unparseable model output" in r.getMessage()]
    assert unparseable
    record = unparseable[0]
    assert getattr(record, "response_sha256", None), "a digest correlates repeats"
    assert getattr(record, "schema_mode", None) is True, "was provider-native mode active?"
    assert getattr(record, "response_chars", 0) > 0
    assert not hasattr(record, "response_head"), "no content at the default level"


def test_the_digest_is_stable_and_distinguishing() -> None:
    """Its whole job is saying 'this is the failure you saw last run'."""
    assert _response_digest("prose, always prose") == _response_digest("prose, always prose")
    assert _response_digest("a") != _response_digest("b")
    assert len(_response_digest("a")) <= 16, "an identifier, not the response"


def test_schema_mode_is_reported_false_when_it_was_never_active(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ReviewIncompleteError):
            LLMReviewEngine(_NeverComplies()).review(_CTX, _cfg(structured_output=False))

    unparseable = [r for r in caplog.records if "unparseable model output" in r.getMessage()]
    assert unparseable
    assert getattr(unparseable[0], "schema_mode", None) is False


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_the_retry_is_on_by_default() -> None:
    """Like the repair, it costs nothing on a healthy run: it fires only on a
    call that already failed AND whose cheap reformat already failed too."""
    assert ReviewConfig(provider="ollama", model="m").retry_without_schema is True


def test_findings_from_a_retry_are_ordinary_findings() -> None:
    findings, _summary = LLMReviewEngine(_CompliesOnlyWithoutTheSchema()).review(_CTX, _cfg())

    assert isinstance(findings[0], ReviewFinding)
    assert findings[0].severity is Severity.high
    assert findings[0].category, "stamped with its originating lens, like any other"
