"""A lens call that blows its OUTPUT ceiling is retried SMALLER too.

Sibling of ``test_timeout_split``. A wall timeout and a blown `max_tokens`
ceiling say the same thing about the payload — one call was asked to cover more
than it could finish — so they earn the same remedy: split the batch and review
the pieces. Before this, a ceiling hit failed the whole lens, and a real dogfood
run lost three of four lenses to it.

Two wrinkles the timeout does not have:

- A truncated response is a *partial answer*, so the findings the model completed
  before the cut travel with the failure and are kept.
- The split is not guaranteed to fix it. A reasoning model spends the same
  `max_tokens` budget on thought, so a fifteen-line diff can truncate too. A
  piece that truncates again is a reachable state, not a theoretical one, and it
  must terminate naming the lever that can actually move — never recurse.
- When the numbers say the ceiling went on thought, the split is skipped
  outright: it cannot help, and attempting it re-spends the ceiling per piece.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
)
from lgtmaybe.core.ports import Message, ProviderTruncated
from lgtmaybe.engine import LLMReviewEngine, clear_interrupt, request_interrupt
from lgtmaybe.engine.engine import ReviewIncompleteError
from tests.fakes import FakeProvider

_TWO_FILE_DIFF = """diff --git a/one.py b/one.py
--- a/one.py
+++ b/one.py
@@ -1,2 +1,4 @@
 import os
+first_change = os.getcwd()
+early_change = os.sep
 print(os.name)
diff --git a/two.py b/two.py
--- a/two.py
+++ b/two.py
@@ -1,2 +1,3 @@
 import sys
+second_change = sys.maxsize
 print(sys.argv)
"""

_ONE_FILE_ONE_HUNK = """diff --git a/one.py b/one.py
--- a/one.py
+++ b/one.py
@@ -1,2 +1,3 @@
 import os
+first_change = os.getcwd()
 print(os.name)
"""

# The adapter's real wording (litellm_provider._map_response): the ceiling named
# is `max_tokens` — in the run that prompted this it was lgtmaybe's own
# configured 16,384 against a model good for 65,536 — and the reasoning count is
# what explains a small diff truncating at all.
_CEILING = (
    "response hit the 16384-token `max_tokens` ceiling (16200 reasoning) before "
    "finishing — the batch is re-reviewed in smaller pieces automatically, so a lens "
    "that keeps doing it is usually generation instability in the model, which a "
    "higher ceiling makes more expensive rather than prevents"
)


# The same failure, but the numbers say the ceiling went on *thought*. Measured
# on a real self-review: five of nine calls spent 25,963–35,463 tokens reasoning
# against a 32,768 ceiling, i.e. the whole budget, before writing one finding.
_REASONING_CEILING = 32768
_REASONING_SPENT = 32194


def _ctx(diff: str, files: list[str]) -> PRContext:
    return PRContext(
        diff=diff,
        changed_files=files,
        base_sha="b",
        head_sha="h",
        repo="o/r",
        pr_number=1,
    )


def _cfg(**overrides: Any) -> ReviewConfig:
    return ReviewConfig(
        provider=Provider.openrouter,
        model="deepseek/deepseek-v4-pro",
        categories=[ReviewCategory.security],
        reflect=False,
        **overrides,
    )


def _finding(path: str, anchor: str, title: str) -> dict[str, Any]:
    return {
        "path": path,
        "line": 2,
        "severity": "medium",
        "title": title,
        "body": "the new binding is never validated",
        "anchor": anchor,
        "failure_scenario": "A caller reads the new binding before it is set.",
    }


def _finding_json(path: str, anchor: str, title: str | None = None) -> str:
    return json.dumps({"findings": [_finding(path, anchor, title or f"unchecked value in {path}")]})


def _cut_off_json(path: str, anchor: str, title: str) -> str:
    """One complete finding, then a second the ceiling chopped mid-object."""
    return '{"findings": [' + json.dumps(_finding(path, anchor, title)) + ', {"path": "one.py", "li'


# Which files' CHANGES a call was given — not merely which paths it names.
# Every call also names the files it was NOT shown (the not-shown manifest), so a
# filename appearing in the prompt no longer means its diff is in it. These fakes
# decide "was this the whole batch or one piece?", so they have to look at the
# added lines.
_ADDED = {
    "one.py": "first_change = os.getcwd()",
    "two.py": "second_change = sys.maxsize",
}


def _shows(diff: str, *paths: str) -> bool:
    return all(_ADDED[path] in diff for path in paths)


class _TruncatesUntilSmaller(FakeProvider):
    """Blows the output ceiling while both files ride one call; answers a half.

    The real shape of the failure: the payload, not the provider, asked for more
    output than one generation could hold.
    """

    def __init__(self, partial: str = "") -> None:
        super().__init__()
        self.diffs: list[str] = []
        self._partial = partial

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        diff = "\n".join(str(m.get("content", "")) for m in messages)
        self.diffs.append(diff)
        if _shows(diff, "one.py", "two.py"):
            raise ProviderTruncated(_CEILING, text=self._partial)
        path = "one.py" if _shows(diff, "one.py") else "two.py"
        anchor = "first_change = os.getcwd()" if path == "one.py" else "second_change = sys.maxsize"
        return ProviderResult(text=_finding_json(path, anchor), input_tokens=5, output_tokens=5)


def test_an_over_ceiling_batch_is_split_and_its_halves_reviewed() -> None:
    """The remedy the error message tells a human to apply by hand, automated."""
    provider = _TruncatesUntilSmaller()
    findings, _summary = LLMReviewEngine(provider).review(
        _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg()
    )

    assert sorted(f.path for f in findings) == ["one.py", "two.py"]
    # The oversized call, then one per half — never the same oversized payload twice.
    assert len(provider.diffs) == 3
    assert sum(_shows(d, "one.py", "two.py") for d in provider.diffs) == 1


def test_findings_completed_before_the_cut_survive_the_split() -> None:
    """A truncated response is a partial answer, not a non-answer.

    The findings the model finished emitting are real, schema-valid, already-paid-for
    work. The parse path already salvages them; the exception path threw them away.
    """
    partial = _cut_off_json("one.py", "early_change = os.sep", "sep is never validated")
    findings, _summary = LLMReviewEngine(_TruncatesUntilSmaller(partial)).review(
        _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg()
    )

    titles = sorted(f.title for f in findings)
    assert "sep is never validated" in titles  # salvaged from the cut-off response
    assert "unchecked value in two.py" in titles  # produced by the split
    # Salvage is stamped like any other finding — it is posted like any other.
    salvaged = next(f for f in findings if f.title == "sep is never validated")
    assert salvaged.category == ReviewCategory.security.value
    assert salvaged.anchored


def test_a_piece_that_truncates_again_is_not_split_further() -> None:
    """One split level, then the failure stands.

    A reachable state, not a theoretical one: a reasoning model spends the same
    `max_tokens` budget on thought, so a piece can exhaust the cap however small
    it is. An unbounded cascade would then spend the whole review chasing a size
    that does not exist — so it stops, and says which lever is left.
    """

    class _AlwaysTruncates(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.diffs: list[str] = []

        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            self.diffs.append("\n".join(str(m.get("content", "")) for m in messages))
            raise ProviderTruncated(_CEILING, text="")

    provider = _AlwaysTruncates()
    with pytest.raises(ReviewIncompleteError) as exc_info:
        LLMReviewEngine(provider).review(_ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg())

    assert len(provider.diffs) == 3  # the batch, then its two halves — and stop
    assert "`max_tokens`" in str(exc_info.value)


def test_an_unsplittable_batch_reports_the_ceiling_with_its_salvage() -> None:
    """A single-hunk file has nothing smaller to try.

    It must report the ceiling rather than loop — and still keep what the model
    completed, exactly as the parse path does for a truncated body.
    """

    class _TruncatesOnce(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.diffs: list[str] = []

        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            self.diffs.append("\n".join(str(m.get("content", "")) for m in messages))
            raise ProviderTruncated(
                _CEILING,
                text=_cut_off_json(
                    "one.py", "first_change = os.getcwd()", "cwd is never validated"
                ),
            )

    provider = _TruncatesOnce()
    findings, summary = LLMReviewEngine(provider).review(
        _ctx(_ONE_FILE_ONE_HUNK, ["one.py"]), _cfg()
    )

    assert len(provider.diffs) == 1  # nothing smaller to try — no recursion
    assert [f.title for f in findings] == ["cwd is never validated"]
    # A partial lens is still a failed lens: the notice fires, so a half-answer is
    # never read as a clean bill of health.
    assert "results may be incomplete" in summary
    # And it names the lever that can still move. Shrinking is spent, so pointing
    # only at `max_input_tokens` would be advice the reader has already taken.
    assert "`max_tokens`" in summary


class _TruncatesOnReasoning(FakeProvider):
    """Every call spends the whole ceiling thinking, whatever the payload is.

    The shape issue #348 measured: a reasoning model's thinking budget is the
    same `max_tokens` ceiling, and it does not shrink when the diff does.
    """

    def __init__(self, partial: str = "") -> None:
        super().__init__()
        self.diffs: list[str] = []
        self._partial = partial

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        self.diffs.append("\n".join(str(m.get("content", "")) for m in messages))
        raise ProviderTruncated(
            _CEILING,
            text=self._partial,
            reasoning_tokens=_REASONING_SPENT,
            output_tokens=_REASONING_CEILING,
        )


def test_a_reasoning_dominated_truncation_is_not_split() -> None:
    """Splitting cannot shrink a thinking budget, so it is not attempted.

    Halving the diff halves the payload, not the reasoning the model does before
    it answers — issue #348 recorded a fifteen-line diff truncating at the same
    ceiling. Splitting anyway burns the full ceiling again on every piece and
    fails identically, which is pure added latency on an already-slow review.
    """
    provider = _TruncatesOnReasoning()
    with pytest.raises(ReviewIncompleteError) as exc_info:
        LLMReviewEngine(provider).review(_ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg())

    assert len(provider.diffs) == 1  # the batch — and no pieces
    # And it names the lever that can actually move. `max_tokens` cannot: the cap
    # does not separate thinking from answering, so raising it buys more thought.
    assert "reasoning_effort" in str(exc_info.value)


def test_a_reasoning_dominated_truncation_keeps_its_salvage() -> None:
    """Not splitting must not become "not salvaging".

    The findings the model completed before the cut are real, already-paid-for
    work on every path — the one that splits and the one that gives up on size.
    """
    partial = _cut_off_json("one.py", "early_change = os.sep", "sep is never validated")
    findings, summary = LLMReviewEngine(_TruncatesOnReasoning(partial)).review(
        _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg()
    )

    assert [f.title for f in findings] == ["sep is never validated"]
    assert "results may be incomplete" in summary
    assert "reasoning_effort" in summary


def test_a_reasoning_dominated_truncation_offers_the_cap_as_well() -> None:
    """Naming only `reasoning_effort` states the pessimistic case as fact.

    Two different failures produce identical numbers. Thinking that *expands to
    fill* whatever ceiling it is given is immune to a bigger cap — the case
    issue #348 measured, and the one where lowering the effort is the only move.
    Thinking with a bounded natural size that merely exceeds this ceiling is the
    opposite: raising the cap fixes it outright, and lowering the effort buys
    the fix in review quality instead. One truncation cannot tell the two apart
    — only re-running at a higher cap can — so the reader is handed both levers
    rather than the pessimistic one asserted as proven.
    """
    with pytest.raises(ReviewIncompleteError) as exc_info:
        LLMReviewEngine(_TruncatesOnReasoning()).review(
            _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg()
        )

    reason = str(exc_info.value)
    assert "reasoning_effort" in reason
    assert "raise `max_tokens`" in reason


def test_a_truncation_that_spent_its_ceiling_on_findings_is_still_split() -> None:
    """The output-length truncation the split was built for is untouched.

    A model that thought briefly and then wrote to the ceiling really did have
    more to say than one response could hold — for that one, less to cover is
    exactly the fix.
    """

    class _TruncatesWithLittleThought(_TruncatesUntilSmaller):
        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            diff = "\n".join(str(m.get("content", "")) for m in messages)
            if _shows(diff, "one.py", "two.py"):
                self.diffs.append(diff)
                raise ProviderTruncated(
                    _CEILING,
                    text="",
                    reasoning_tokens=2048,
                    output_tokens=_REASONING_CEILING,
                )
            return super().complete(messages, model, **opts)

    provider = _TruncatesWithLittleThought()
    findings, _summary = LLMReviewEngine(provider).review(
        _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg()
    )

    assert sorted(f.path for f in findings) == ["one.py", "two.py"]
    assert len(provider.diffs) == 3  # the batch, then one call per half


def test_a_piece_that_truncates_on_reasoning_names_the_lever_too() -> None:
    """The diagnosis has to reach the piece, not only the whole batch.

    A piece has nothing smaller to try, so it reports and stops either way — but
    *what* it reports is the only thing the reader gets. Falling through to the
    generic "raise `max_tokens`" there would send them to the one knob that
    provably does not move this, after the split has already been paid for.

    The batch's own truncation is answer-shaped (little thinking), so the split
    is right to happen; the pieces are where the thinking wall shows up.
    """

    class _BatchRunsLongThenPiecesRunDeep(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.diffs: list[str] = []

        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            diff = "\n".join(str(m.get("content", "")) for m in messages)
            self.diffs.append(diff)
            whole_batch = _shows(diff, "one.py", "two.py")
            raise ProviderTruncated(
                _CEILING,
                text="",
                reasoning_tokens=2048 if whole_batch else _REASONING_SPENT,
                output_tokens=_REASONING_CEILING,
            )

    provider = _BatchRunsLongThenPiecesRunDeep()
    with pytest.raises(ReviewIncompleteError) as exc_info:
        LLMReviewEngine(provider).review(_ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg())

    assert len(provider.diffs) == 3  # the batch, then its two halves — and stop
    assert "reasoning_effort" in str(exc_info.value)


def test_a_truncation_with_no_reasoning_breakdown_is_still_split() -> None:
    """Silence from the route is not evidence of thinking.

    Most routes report no reasoning count at all. Reading that as "reasoning
    dominated" would switch off the split for every provider that stays quiet.
    """
    provider = _TruncatesUntilSmaller()
    findings, _summary = LLMReviewEngine(provider).review(
        _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg()
    )

    assert sorted(f.path for f in findings) == ["one.py", "two.py"]
    assert len(provider.diffs) == 3


def test_a_piece_that_fails_is_still_reported() -> None:
    """Half a batch reviewed is not a reviewed batch — the notice still fires."""

    class _OneHalfRefuses(FakeProvider):
        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            diff = "\n".join(str(m.get("content", "")) for m in messages)
            if _shows(diff, "one.py", "two.py"):
                raise ProviderTruncated(_CEILING, text="")
            if _shows(diff, "two.py"):
                raise RuntimeError("insufficient_quota")
            return ProviderResult(
                text=_finding_json("one.py", "first_change = os.getcwd()"),
                input_tokens=5,
                output_tokens=5,
            )

    findings, summary = LLMReviewEngine(_OneHalfRefuses()).review(
        _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg()
    )

    assert [f.path for f in findings] == ["one.py"]  # the half that answered
    assert "results may be incomplete" in summary
    assert "insufficient_quota" in summary  # naming the piece's real failure


# ---------------------------------------------------------------------------
# The reasoning-bound sibling of the split: one step down, then stop.
# ---------------------------------------------------------------------------


class _TruncatesUntilEffortDrops(FakeProvider):
    """Spends the whole ceiling thinking until asked to think less.

    The provider's own shape: it holds the configured effort and hands back the
    per-call opts that step it down, exactly as the litellm adapter does — the
    engine never knows whether that is a flat `reasoning_effort` or OpenRouter's
    nested `reasoning` object.
    """

    _NEVER = object()  # distinct from every effort value, including None

    def __init__(self, effort: str | None = "medium", answers_at: Any = "low") -> None:
        super().__init__()
        self.efforts: list[str | None] = []
        self._effort = effort
        self._answers_at = answers_at

    def lower_reasoning_effort(self) -> dict[str, Any] | None:
        if self._effort in (None, "none"):
            return None
        ladder = ["none", "minimal", "low", "medium", "high", "xhigh"]
        return {"reasoning_effort": ladder[ladder.index(self._effort) - 1]}

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        effort = opts.get("reasoning_effort", self._effort)
        self.efforts.append(effort)
        if effort == self._answers_at:
            return ProviderResult(
                text=_finding_json("one.py", "first_change = os.getcwd()"),
                input_tokens=10,
                output_tokens=10,
            )
        raise ProviderTruncated(
            _CEILING,
            text="",
            reasoning_tokens=_REASONING_SPENT,
            output_tokens=_REASONING_CEILING,
        )


def test_a_reasoning_bound_truncation_retries_once_at_a_lower_effort() -> None:
    """The missing sibling of the split.

    A payload-bound truncation shrinks the payload and re-reviews. A
    reasoning-bound one was diagnosed correctly and then abandoned, losing the
    lens for that round. It changes the one variable that can help instead.
    """
    provider = _TruncatesUntilEffortDrops()

    findings, summary = LLMReviewEngine(provider).review(
        _ctx(_ONE_FILE_ONE_HUNK, ["one.py"]), _cfg()
    )

    assert provider.efforts == ["medium", "low"]  # one step down, never up
    assert [f.title for f in findings] == ["unchecked value in one.py"]
    # Not silent: a lens that answered only after being told to think less is a
    # fact about this review, and the summary says so.
    assert "reasoning_effort" in summary


def test_a_second_reasoning_bound_truncation_reports_and_stops() -> None:
    """One attempt, not a cascade — the same posture as the split's one level."""
    provider = _TruncatesUntilEffortDrops(answers_at=_TruncatesUntilEffortDrops._NEVER)

    with pytest.raises(ReviewIncompleteError) as exc_info:
        LLMReviewEngine(provider).review(_ctx(_ONE_FILE_ONE_HUNK, ["one.py"]), _cfg())

    assert provider.efforts == ["medium", "low"]  # and no third
    assert "reasoning_effort" in str(exc_info.value)


def test_a_failed_step_down_is_not_reported_as_a_recovery() -> None:
    """The notice says findings came from the lower setting. A retry that
    truncated again produced none, so claiming it would be a second wrong notice
    stacked on the failure the run already reports."""

    class _TruncatesEverywhereButAnswersOneFile(_TruncatesUntilEffortDrops):
        """two.py answers at any effort; one.py truncates at every effort.

        Both lenses have to be in one run, or the review raises before a summary
        is ever built.
        """

        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            diff = "\n".join(str(m.get("content", "")) for m in messages)
            if "second_change" in diff and "first_change" not in diff:
                return ProviderResult(
                    text=_finding_json("two.py", "second_change = sys.maxsize"),
                    input_tokens=5,
                    output_tokens=5,
                )
            self.efforts.append(opts.get("reasoning_effort", self._effort))
            raise ProviderTruncated(
                _CEILING,
                text="",
                reasoning_tokens=_REASONING_SPENT,
                output_tokens=_REASONING_CEILING,
            )

    provider = _TruncatesEverywhereButAnswersOneFile()
    ctx = _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"])

    _findings, summary = LLMReviewEngine(provider).review(
        ctx,
        _cfg(max_input_tokens=60),  # one batch per file, so one lens survives
    )

    assert provider.efforts == ["medium", "low"]  # it did step down, and failed
    assert "re-run once at a lower" not in summary
    assert "results may be incomplete" in summary


def test_no_retry_when_reasoning_effort_is_unset() -> None:
    """Byte-identical behaviour for the users who never configured it: there is
    no lower level to step to, so nothing is retried and nothing is spent."""
    provider = _TruncatesUntilEffortDrops(effort=None, answers_at=_TruncatesUntilEffortDrops._NEVER)

    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(_ctx(_ONE_FILE_ONE_HUNK, ["one.py"]), _cfg())

    assert provider.efforts == [None]  # one call, no step-down


def test_the_step_down_keeps_what_the_cut_call_completed() -> None:
    """Salvage still applies: the findings finished before the cut are real work,
    and they merge with the retry's rather than being replaced by them."""

    class _SalvagesThenAnswers(_TruncatesUntilEffortDrops):
        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            if opts.get("reasoning_effort", self._effort) == "low":
                return ProviderResult(
                    text=_finding_json("two.py", "second_change = sys.maxsize", "found at low"),
                    input_tokens=10,
                    output_tokens=10,
                )
            self.efforts.append("medium")
            raise ProviderTruncated(
                _CEILING,
                text=_cut_off_json("one.py", "first_change = os.getcwd()", "found before the cut"),
                reasoning_tokens=_REASONING_SPENT,
                output_tokens=_REASONING_CEILING,
            )

    provider = _SalvagesThenAnswers()

    findings, _summary = LLMReviewEngine(provider).review(
        _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg()
    )

    assert {f.title for f in findings} == {"found before the cut", "found at low"}


def test_a_payload_bound_truncation_splits_and_does_not_step_down() -> None:
    """The two remedies stay disjoint. A payload-bound truncation says nothing
    about the thinking budget, so lowering the effort there would pay for a fix
    in review quality that the split provides for free."""

    class _SplitsAndCountsStepDowns(_TruncatesUntilSmaller):
        asked = 0

        def lower_reasoning_effort(self) -> dict[str, Any] | None:
            type(self).asked += 1
            return {"reasoning_effort": "low"}

    provider = _SplitsAndCountsStepDowns()

    findings, _summary = LLMReviewEngine(provider).review(
        _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg()
    )

    assert sorted(f.path for f in findings) == ["one.py", "two.py"]  # the split still ran
    assert _SplitsAndCountsStepDowns.asked == 0


def test_the_step_down_does_not_spend_past_a_whole_review_ceiling() -> None:
    """This path reaches `_complete_lens` directly, so it does not get the
    re-check the split's pieces inherit by re-entering `_review_lens`.

    A retry that begins past the deadline, the token budget, or a termination
    signal would spend after the review was told to stop — which is the guarantee
    the partial-results notice rests on."""
    provider = _TruncatesUntilEffortDrops(answers_at=_TruncatesUntilEffortDrops._NEVER)

    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(
            _ctx(_ONE_FILE_ONE_HUNK, ["one.py"]),
            # A budget of one token: the first call blows it, so the retry must
            # not run — the truncation is reported, nothing more is spent.
            _cfg(max_review_tokens=1),
        )

    assert provider.efforts == ["medium"]  # no step-down attempted


def test_the_step_down_does_not_outlive_the_review_deadline() -> None:
    """The budget is one of three stops `_skip_reason` enforces. This is the
    second: a deadline already passed when the truncation lands must not buy a
    second billed call."""

    class _SlowTruncation(_TruncatesUntilEffortDrops):
        """Overruns the 1s ceiling before it truncates, exactly as a real
        reasoning runaway does — it is the slowest call in the run by far."""

        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            time.sleep(1.2)
            return super().complete(messages, model, **opts)

    provider = _SlowTruncation(answers_at=_TruncatesUntilEffortDrops._NEVER)

    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(
            _ctx(_ONE_FILE_ONE_HUNK, ["one.py"]), _cfg(max_review_seconds=1)
        )

    assert provider.efforts == ["medium"]


def test_the_step_down_respects_a_termination_signal() -> None:
    """And the third: a cancelled or timed-out CI job. An interrupted run posts
    what it has — it does not spend one more call on the way out."""

    class _TruncatesThenAsksToStop(_TruncatesUntilEffortDrops):
        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            request_interrupt()
            return super().complete(messages, model, **opts)

    provider = _TruncatesThenAsksToStop(answers_at=_TruncatesUntilEffortDrops._NEVER)
    try:
        with pytest.raises(ReviewIncompleteError):
            LLMReviewEngine(provider).review(_ctx(_ONE_FILE_ONE_HUNK, ["one.py"]), _cfg())
    finally:
        clear_interrupt()

    assert provider.efforts == ["medium"]
