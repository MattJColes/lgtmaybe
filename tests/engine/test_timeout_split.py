"""A lens call that blows its wall clock is retried SMALLER, not repeated.

Re-sending the identical oversized request against the identical budget cannot
succeed — the adapter therefore fails a wall timeout after one attempt. But
failing the lens outright throws away the whole batch's review, and the batch is
exactly what was too big. So the engine splits it and reviews the pieces, each
with its own fresh budget: the one retry that can actually work.
"""

from __future__ import annotations

import json
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
        prompt_cache=False,
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
        if "one.py" in diff and "two.py" in diff:
            raise ProviderWallTimeout("provider request exceeded 1800s (waited 1800.001s)")
        path = "one.py" if "one.py" in diff else "two.py"
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
    assert sum("one.py" in d and "two.py" in d for d in provider.diffs) == 1


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
            if "one.py" in diff and "two.py" in diff:
                raise ProviderWallTimeout("provider request exceeded 1800s (waited 1800.001s)")
            if "two.py" in diff:
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

    assert len(provider.diffs) == 1
