"""A prompt the model's context window cannot hold is retried SMALLER.

Third sibling of ``test_timeout_split`` and ``test_truncation_split``. A wall
timeout, a blown output ceiling and a blown INPUT window all say the same thing
about the payload — one call was asked to carry more than the model could take
— so they earn the same remedy: split the batch and review the pieces.

Before this, the failure arrived as litellm's ``ContextWindowExceededError``, a
``BadRequestError`` subclass the adapter (rightly) never retries in place and
(wrongly) stamped unrecoverable — so the engine gave the lens up as a dead key
would be, when the split it already had was exactly the fix.
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
from lgtmaybe.core.ports import Message, ProviderInputTooLarge
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

_ADDED = {
    "one.py": "first_change = os.getcwd()",
    "two.py": "second_change = sys.maxsize",
}


def _shows(diff: str, *paths: str) -> bool:
    return all(_ADDED[path] in diff for path in paths)


def _ctx() -> PRContext:
    return PRContext(
        diff=_TWO_FILE_DIFF,
        changed_files=["one.py", "two.py"],
        base_sha="b",
        head_sha="h",
        repo="o/r",
        pr_number=1,
    )


def _cfg() -> ReviewConfig:
    return ReviewConfig(
        provider=Provider.anthropic,
        model="claude-sonnet-4-5",
        categories=[ReviewCategory.security],
        reflect=False,
    )


def _finding_json(path: str) -> str:
    return json.dumps(
        {
            "findings": [
                {
                    "path": path,
                    "line": 2,
                    "severity": "medium",
                    "title": f"unchecked value in {path}",
                    "body": "the new binding is never validated",
                    "anchor": _ADDED[path],
                    "failure_scenario": "A caller reads the new binding before it is set.",
                }
            ]
        }
    )


class _TooLongUntilSmaller(FakeProvider):
    """Refuses the prompt while both files ride one call; answers a half."""

    def __init__(self) -> None:
        super().__init__()
        self.diffs: list[str] = []

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        diff = "\n".join(str(m.get("content", "")) for m in messages)
        self.diffs.append(diff)
        if _shows(diff, "one.py", "two.py"):
            raise ProviderInputTooLarge("prompt is too long: 214000 tokens > 200000 maximum")
        path = "one.py" if _shows(diff, "one.py") else "two.py"
        return ProviderResult(text=_finding_json(path), input_tokens=5, output_tokens=5)


def test_an_over_window_batch_is_split_and_its_halves_reviewed() -> None:
    provider = _TooLongUntilSmaller()
    findings, summary = LLMReviewEngine(provider).review(_ctx(), _cfg())

    assert sorted(f.path for f in findings) == ["one.py", "two.py"]
    # The oversized call, then one per half — never the same oversized prompt twice.
    assert len(provider.diffs) == 3
    assert sum(_shows(d, "one.py", "two.py") for d in provider.diffs) == 1
    assert "incomplete" not in summary.lower()


class _AlwaysTooLong(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        self.calls += 1
        raise ProviderInputTooLarge("prompt is too long")


def test_a_piece_that_is_still_too_long_is_reported_not_recursed() -> None:
    """One split level, then the failure stands — named, never an endless cascade."""
    provider = _AlwaysTooLong()
    with pytest.raises(ReviewIncompleteError) as exc_info:
        LLMReviewEngine(provider).review(_ctx(), _cfg())

    # The whole batch, then one attempt per half. No third level.
    assert provider.calls == 3
    assert "too long" in str(exc_info.value)


# One file, ONE hunk — every brand-new file looks like this. A truncation on it
# has nothing smaller to try (see test_truncation_split); a refused prompt does.
_ONE_BIG_HUNK = (
    """diff --git a/new.py b/new.py
--- /dev/null
+++ b/new.py
@@ -0,0 +1,12 @@
+first_change = 1
"""
    + "".join(f"+padding_line_{i} = {i}\n" for i in range(10))
    + "+last_change = 12\n"
)


class _RefusesTheWholeHunk(FakeProvider):
    """Refuses the prompt while the whole hunk is in it; answers any slice."""

    def __init__(self) -> None:
        super().__init__()
        self.diffs: list[str] = []

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        diff = "\n".join(str(m.get("content", "")) for m in messages)
        self.diffs.append(diff)
        if "first_change = 1" in diff and "last_change = 12" in diff:
            raise ProviderInputTooLarge("prompt is too long: 214000 tokens > 200000 maximum")
        return ProviderResult(text='{"findings": []}', input_tokens=5, output_tokens=5)


def test_a_refused_lone_hunk_is_sliced_inside() -> None:
    """A context-window refusal is purely about input size, so cutting inside the
    one hunk is guaranteed to help — the truncation path's "nothing smaller to
    try" would leave a new file entirely unreviewed here."""
    provider = _RefusesTheWholeHunk()
    ctx = PRContext(
        diff=_ONE_BIG_HUNK,
        changed_files=["new.py"],
        base_sha="b",
        head_sha="h",
        repo="o/r",
        pr_number=1,
    )
    _findings, summary = LLMReviewEngine(provider).review(ctx, _cfg())

    assert len(provider.diffs) >= 3  # the whole hunk, then at least two slices
    assert sum("first_change = 1" in d and "last_change = 12" in d for d in provider.diffs) == 1
    assert "incomplete" not in summary.lower()
