"""A lens call that blows its wall clock is retried SMALLER, not repeated.

Re-sending the identical oversized request against the identical budget cannot
succeed — the adapter therefore fails a wall timeout after one attempt. But
failing the lens outright throws away the whole batch's review, and the batch is
exactly what was too big. So the engine splits it and reviews the pieces, each
with its own fresh budget: the one retry that can actually work.
"""

from __future__ import annotations

import json
import threading
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
from lgtmaybe.core.ports import Message, ProviderWallTimeout
from lgtmaybe.engine import LLMReviewEngine
from lgtmaybe.engine.engine import ReviewIncompleteError
from tests.fakes import FakeProvider

_TWO_FILE_DIFF = """diff --git a/one.py b/one.py
--- a/one.py
+++ b/one.py
@@ -1,2 +1,3 @@
 import os
+first_change = os.getcwd()
 print(os.name)
diff --git a/two.py b/two.py
--- a/two.py
+++ b/two.py
@@ -1,2 +1,3 @@
 import sys
+second_change = sys.maxsize
 print(sys.argv)
"""

_ONE_FILE_TWO_HUNKS = """diff --git a/one.py b/one.py
--- a/one.py
+++ b/one.py
@@ -1,2 +1,3 @@
 import os
+first_change = os.getcwd()
 print(os.name)
@@ -40,2 +41,3 @@
 def later():
+    second_change = None
     return None
"""


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


def _finding_json(path: str, anchor: str) -> str:
    return json.dumps(
        {
            "findings": [
                {
                    "path": path,
                    "line": 2,
                    "severity": "medium",
                    "title": f"unchecked value in {path}",
                    "body": "the new binding is never validated",
                    "anchor": anchor,
                    "failure_scenario": "A caller reads the new binding before it is set.",
                }
            ]
        }
    )


def _piece_json(diff: str) -> str:
    """The answer a split piece gives, anchored in whichever file it carries."""
    path = "one.py" if _shows(diff, "one.py") else "two.py"
    anchor = "first_change = os.getcwd()" if path == "one.py" else "second_change = sys.maxsize"
    return _finding_json(path, anchor)


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


class _TimeoutUntilSmaller(FakeProvider):
    """Times out while both files are in one call; answers a single-file call.

    Stands in for the real shape of the failure: the payload, not the provider,
    is what could not finish inside the budget.
    """

    def __init__(self) -> None:
        super().__init__()
        self.diffs: list[str] = []

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        diff = "\n".join(str(m.get("content", "")) for m in messages)
        self.diffs.append(diff)
        if _shows(diff, "one.py", "two.py"):
            raise ProviderWallTimeout("provider request exceeded 1800s (waited 1800.001s)")
        path = "one.py" if _shows(diff, "one.py") else "two.py"
        anchor = "first_change = os.getcwd()" if path == "one.py" else "second_change = sys.maxsize"
        return ProviderResult(text=_finding_json(path, anchor), input_tokens=5, output_tokens=5)


def test_a_timed_out_batch_is_split_and_its_halves_reviewed() -> None:
    provider = _TimeoutUntilSmaller()
    findings, summary = LLMReviewEngine(provider).review(
        _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg()
    )

    assert sorted(f.path for f in findings) == ["one.py", "two.py"]
    # The oversized call, then one per half — never the same oversized payload twice.
    assert len(provider.diffs) == 3
    assert sum(_shows(d, "one.py", "two.py") for d in provider.diffs) == 1


def test_the_split_is_reported_not_silent() -> None:
    """A review that had to shrink its batches says so — a quiet split hides that
    the model is being pushed past its budget on every run."""
    _findings, summary = LLMReviewEngine(_TimeoutUntilSmaller()).review(
        _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg()
    )
    assert "timed out" in summary.lower()


def test_a_piece_that_fails_is_still_reported() -> None:
    """Half a batch reviewed is not a reviewed batch.

    The split's whole risk: findings come back, so the run looks healthy, while a
    piece nobody reviewed is silently missing. The failure has to reach the
    incomplete-results notice — otherwise a shrunk batch can report a clean bill
    of health for code no model ever saw.
    """

    class _OneHalfRefuses(FakeProvider):
        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            diff = "\n".join(str(m.get("content", "")) for m in messages)
            if _shows(diff, "one.py", "two.py"):
                raise ProviderWallTimeout("provider request exceeded 1800s (waited 1800.001s)")
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


def test_a_single_file_batch_splits_by_hunk() -> None:
    """One file can still be too big; its hunks are the next unit down."""

    class _TimeoutUntilOneHunk(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.diffs: list[str] = []

        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            diff = "\n".join(str(m.get("content", "")) for m in messages)
            self.diffs.append(diff)
            # Both of the file's changed lines in one payload = the whole file.
            # (Counting `@@` would not work: every lens prompt carries a worked
            # example with a real hunk header.)
            if "os.getcwd" in diff and "second_change = None" in diff:
                raise ProviderWallTimeout("provider request exceeded 1800s (waited 1800.001s)")
            anchor = "first_change = os.getcwd()" if "os.getcwd" in diff else "second_change = None"
            return ProviderResult(
                text=_finding_json("one.py", anchor), input_tokens=5, output_tokens=5
            )

    provider = _TimeoutUntilOneHunk()
    findings, _summary = LLMReviewEngine(provider).review(
        _ctx(_ONE_FILE_TWO_HUNKS, ["one.py"]), _cfg()
    )

    # One finding per hunk, both anchored in the real file — nothing was lost by
    # reviewing the file in two pieces.
    assert [f.path for f in findings] == ["one.py", "one.py"]
    assert all(f.anchored for f in findings)
    assert len(provider.diffs) == 3  # the whole file, then each hunk


def test_a_piece_that_times_out_again_is_not_split_further() -> None:
    """One split level, then the failure stands: an unbounded cascade would spend
    the whole review budget on a model that cannot answer at any size."""

    class _AlwaysTimesOut(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.diffs: list[str] = []

        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            self.diffs.append("\n".join(str(m.get("content", "")) for m in messages))
            raise ProviderWallTimeout("provider request exceeded 1800s (waited 1800.001s)")

    provider = _AlwaysTimesOut()
    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(_ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg())

    assert len(provider.diffs) == 3  # the batch, then its two halves — and stop


class _TimeoutThenSlowPieces(FakeProvider):
    """Times out on the whole batch; each piece then takes real wall time.

    For asserting pieces do NOT overlap. The delay only has to be long enough
    that a concurrent pair would be caught in the act — and getting that wrong
    can only cost a missed detection, never a spurious failure, which is the
    safe direction for a clock in a test. Proving overlap is the direction that
    would flake, so it uses a rendezvous instead (see below).
    """

    def __init__(self, delay: float = 0.05) -> None:
        super().__init__()
        self._delay = delay
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_in_flight = 0

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        diff = "\n".join(str(m.get("content", "")) for m in messages)
        if _shows(diff, "one.py", "two.py"):
            raise ProviderWallTimeout("provider request exceeded 1800s (waited 1800.001s)")
        with self._lock:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            time.sleep(self._delay)
            return ProviderResult(text=_piece_json(diff), input_tokens=5, output_tokens=5)
        finally:
            with self._lock:
                self._in_flight -= 1


# How long a piece waits at the rendezvous for its sibling to arrive. Generous
# enough that a loaded runner scheduling the second thread late cannot fail the
# test, and bounded so a REGRESSION to serial pieces fails in seconds with a
# real message instead of hanging the job until CI kills it.
_RENDEZVOUS_TIMEOUT = 5.0


class _TimeoutThenPairedPieces(FakeProvider):
    """Times out on the whole batch; its pieces then have to meet each other.

    The rendezvous *is* the assertion: each piece blocks at the barrier until
    the other arrives, so overlap is proven by both getting through rather than
    inferred from a stopwatch — no sleep long enough to be safe on a loaded
    runner, and no sleep short enough to flake.

    A serial split can never get both pieces there. The barrier's own timeout
    turns that into a recorded failure the test can name, rather than the
    deadlock an unbounded ``wait()`` would leave for the CI job to time out on.
    """

    def __init__(self) -> None:
        super().__init__()
        self._barrier = threading.Barrier(2, timeout=_RENDEZVOUS_TIMEOUT)
        self.serialised = False

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        diff = "\n".join(str(m.get("content", "")) for m in messages)
        if _shows(diff, "one.py", "two.py"):
            raise ProviderWallTimeout("provider request exceeded 1800s (waited 1800.001s)")
        try:
            self._barrier.wait()
        except threading.BrokenBarrierError:
            # Recorded, not raised: the review runs to completion either way, so
            # the test reports "the pieces ran serially" instead of a piece
            # failing for a reason that reads like a provider error.
            self.serialised = True
        return ProviderResult(text=_piece_json(diff), input_tokens=5, output_tokens=5)


def test_the_pieces_of_a_split_batch_are_reviewed_concurrently() -> None:
    """A split cost N sequential model calls, inside a worker already holding a
    pool slot — on a run where most lenses split, the biggest wall-clock
    multiplier in the system. The pieces are independent, so they overlap."""
    provider = _TimeoutThenPairedPieces()
    findings, _summary = LLMReviewEngine(provider).review(
        _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg()
    )

    assert not provider.serialised, (
        "the split's pieces ran serially: neither piece was inside the provider "
        f"while the other was, within {_RENDEZVOUS_TIMEOUT}s"
    )
    assert sorted(f.path for f in findings) == ["one.py", "two.py"]


def test_one_worker_reviews_the_split_pieces_one_at_a_time() -> None:
    """The split is bounded by the review's own concurrency, not by piece count.

    Whatever the fan-out is allowed, the split is allowed — never more. Pinned
    with `max_concurrency=1` outright: this used to lean on ollama resolving to a
    single worker, which is no longer true, and serialism was always what the
    test meant rather than any particular provider.
    """
    provider = _TimeoutThenSlowPieces()
    LLMReviewEngine(provider).review(
        _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]),
        ReviewConfig(
            provider=Provider.ollama,
            model="qwen3-coder",
            max_concurrency=1,
            categories=[ReviewCategory.security],
            reflect=False,
        ),
    )

    assert provider.max_in_flight == 1


def test_an_explicit_concurrency_lifts_the_split_off_the_auto_default() -> None:
    """The user's number wins over the provider default, in the split as in the
    fan-out.

    A local server's throughput is set on the server (`OLLAMA_NUM_PARALLEL`,
    llama.cpp `-np`, vLLM's batching), so `max_concurrency` is how the user tells
    lgtmaybe what their server can take. Honouring it in only one of the two
    pools would make one setting mean two different things.
    """
    provider = _TimeoutThenPairedPieces()
    LLMReviewEngine(provider).review(
        _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]),
        ReviewConfig(
            provider=Provider.ollama,
            model="qwen3-coder",
            categories=[ReviewCategory.security],
            reflect=False,
            max_concurrency=2,
        ),
    )

    assert not provider.serialised, (
        "an explicit max_concurrency=2 did not reach the split: its pieces still "
        f"ran one at a time (rendezvous timed out after {_RENDEZVOUS_TIMEOUT}s)"
    )


def test_a_split_starting_past_the_deadline_costs_nothing() -> None:
    """Concurrent pieces must still answer to the whole-review deadline.

    Serially, a piece was held back by the previous piece's own check. Running
    together they check at the same instant — so the check has to be the
    deadline itself, or a review already over budget would fan out past it.
    """

    class _SlowTimeout(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.diffs: list[str] = []

        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            self.diffs.append("\n".join(str(m.get("content", "")) for m in messages))
            time.sleep(1.2)
            raise ProviderWallTimeout("provider request exceeded 1800s (waited 1800.001s)")

    provider = _SlowTimeout()
    with pytest.raises(ReviewIncompleteError) as exc_info:
        LLMReviewEngine(provider).review(
            _ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg(max_review_seconds=1)
        )

    assert len(provider.diffs) == 1  # the batch — both pieces skipped, not run
    assert "deadline" in str(exc_info.value)


def test_an_ordinary_failure_is_not_split() -> None:
    """Only a blown wall clock implies "too big"; a 429 or a bad key does not, and
    splitting one would multiply the failing calls."""

    class _Rejects(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.diffs: list[str] = []

        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            self.diffs.append("\n".join(str(m.get("content", "")) for m in messages))
            raise RuntimeError("insufficient_quota")

    provider = _Rejects()
    with pytest.raises(ReviewIncompleteError):
        LLMReviewEngine(provider).review(_ctx(_TWO_FILE_DIFF, ["one.py", "two.py"]), _cfg())

    # The original call plus the rescue wave's one more go — and both saw the
    # WHOLE diff. A split would have sent a halved one, which is the thing under
    # test here; the rescue only ever re-sends the same request.
    assert len(provider.diffs) == 2
    assert provider.diffs[0] == provider.diffs[1]
