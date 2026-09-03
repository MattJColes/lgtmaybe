"""The batching budget is fitted to the model's context window.

`max_input_tokens` defaults to a flat number that knows nothing about the model.
A provider that can say how much prompt its model takes (the litellm adapter
reads it off litellm's model map, or off ollama's `num_ctx`) is asked, and the
default is shrunk to fit — so a batch is never built that the model refuses,
or that ollama silently truncates. A configured value is the user's call and
is left alone; a provider with no opinion changes nothing.
"""

from __future__ import annotations

from typing import Any

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
)
from lgtmaybe.core.ports import Message
from lgtmaybe.engine import LLMReviewEngine
from lgtmaybe.engine.compress import count_tokens
from tests.fakes import FakeProvider

# Two equal-sized files, padded so the per-file token count is well clear of
# the counting noise between tokenizers (CI has the real encoder; an
# egress-restricted box falls back to the character estimate).
_TWO_FILE_DIFF = """diff --git a/one.py b/one.py
--- a/one.py
+++ b/one.py
@@ -1,14 +1,15 @@
 import os
+first_change = os.getcwd()
 pad_0 = 0
 pad_1 = 1
 pad_2 = 2
 pad_3 = 3
 pad_4 = 4
 pad_5 = 5
 pad_6 = 6
 pad_7 = 7
 pad_8 = 8
 pad_9 = 9
 pad_10 = 10
 pad_11 = 11
 print(os.name)
diff --git a/two.py b/two.py
--- a/two.py
+++ b/two.py
@@ -1,14 +1,15 @@
 import sys
+second_change = sys.maxsize
 pad_0 = 0
 pad_1 = 1
 pad_2 = 2
 pad_3 = 3
 pad_4 = 4
 pad_5 = 5
 pad_6 = 6
 pad_7 = 7
 pad_8 = 8
 pad_9 = 9
 pad_10 = 10
 pad_11 = 11
 print(sys.argv)
"""


def _ctx() -> PRContext:
    return PRContext(
        diff=_TWO_FILE_DIFF,
        changed_files=["one.py", "two.py"],
        base_sha="b",
        head_sha="h",
        repo="o/r",
        pr_number=1,
    )


def _cfg(**overrides: Any) -> ReviewConfig:
    return ReviewConfig(
        provider=Provider.ollama,
        model="qwen3:8b",
        categories=[ReviewCategory.security],
        reflect=False,
        context_lines=0,
        **overrides,
    )


class _Counting(FakeProvider):
    def __init__(self, budget: int | None) -> None:
        super().__init__()
        self._budget = budget
        self.calls = 0

    def input_budget(self) -> int | None:
        return self._budget

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        self.calls += 1
        return ProviderResult(text='{"findings": []}', input_tokens=1, output_tokens=1)


def _one_file_fits_two_do_not() -> int:
    """A budget one file clears and two together overflow, in whatever counter
    this box has — the two files are the same size by construction."""
    return int(count_tokens(_TWO_FILE_DIFF) * 0.6)


def test_a_small_window_splits_what_the_default_would_send_whole() -> None:
    """Two files fit one call at the default budget; a provider whose model
    takes only one of them at a time gets them one per call."""
    provider = _Counting(budget=_one_file_fits_two_do_not())
    LLMReviewEngine(provider).review(_ctx(), _cfg())
    assert provider.calls == 2


def test_a_generous_window_leaves_the_default_alone() -> None:
    provider = _Counting(budget=1_000_000)
    LLMReviewEngine(provider).review(_ctx(), _cfg())
    assert provider.calls == 1


def test_a_configured_budget_is_the_users_call() -> None:
    """An explicit `max_input_tokens` is honoured even past what the provider
    reports — the user may know something the map does not."""
    provider = _Counting(budget=_one_file_fits_two_do_not())
    LLMReviewEngine(provider).review(_ctx(), _cfg(max_input_tokens=100_000))
    assert provider.calls == 1


def test_a_provider_with_no_opinion_changes_nothing() -> None:
    provider = _Counting(budget=None)
    LLMReviewEngine(provider).review(_ctx(), _cfg())
    assert provider.calls == 1


def test_a_provider_that_cannot_answer_changes_nothing() -> None:
    class _Plain(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
            self.calls += 1
            return ProviderResult(text='{"findings": []}', input_tokens=1, output_tokens=1)

    provider = _Plain()
    LLMReviewEngine(provider).review(_ctx(), _cfg())
    assert provider.calls == 1
