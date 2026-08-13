"""Tests for the split (cache-shaped) prompt layout and the cache warm-up primer.

With ``prompt_cache`` on (the default) every review call shares one expensive
prefix — shared system preamble + wrapped diff — and carries its lens-specific
instruction as the final user block, so caching providers serve the prefix from
cache across the whole fan-out. ``prompt_cache: false`` restores the legacy
shape (lens text in the system prompt) byte-for-byte.
"""

from __future__ import annotations

import threading

import pytest

from lgtmaybe.core.models import (
    CustomLens,
    PRContext,
    Provider,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
)
from lgtmaybe.engine import LLMReviewEngine
from lgtmaybe.engine.compress import count_tokens
from lgtmaybe.engine.prompt import build_shared_preamble, build_system_prompt
from tests.fakes import FakeProvider

_CTX = PRContext(
    diff="@@ -1,3 +1,4 @@\n context\n+new line\n context\n",
    changed_files=["a.py"],
    base_sha="abc",
    head_sha="def",
    repo="org/repo",
    pr_number=1,
)


def _cfg(**overrides: object) -> ReviewConfig:
    defaults: dict[str, object] = {
        "provider": Provider.openai,
        "model": "m",
        "categories": [ReviewCategory.security, ReviewCategory.performance],
        "reflect": False,
    }
    defaults.update(overrides)
    return ReviewConfig(**defaults)  # type: ignore[arg-type]


class TestSplitShape:
    def test_split_shape_by_default(self) -> None:
        provider = FakeProvider()
        LLMReviewEngine(provider).review(_CTX, _cfg())
        for call in provider.calls:
            messages = call["messages"]
            assert [m["role"] for m in messages] == ["system", "user", "user"]
            assert messages[0]["content"] == build_shared_preamble()
            assert "DIFF_START" in messages[1]["content"]
            assert "DIFF_START" not in messages[2]["content"]

    def test_shared_prefix_is_identical_across_lenses(self) -> None:
        """The whole point: system + first user block must be byte-identical
        across every lens call so a caching provider serves them from cache."""
        provider = FakeProvider()
        LLMReviewEngine(provider).review(_CTX, _cfg())
        prefixes = {
            (c["messages"][0]["content"], c["messages"][1]["content"]) for c in provider.calls
        }
        assert len(prefixes) == 1

    def test_lens_block_differs_per_lens_and_carries_the_checklist(self) -> None:
        provider = FakeProvider()
        LLMReviewEngine(provider).review(_CTX, _cfg())
        blocks = [c["messages"][2]["content"] for c in provider.calls]
        assert len(set(blocks)) == 2
        joined = "\n".join(blocks)
        assert "Security review" in joined
        assert "Performance" in joined

    def test_prompt_cache_off_restores_the_legacy_shape(self) -> None:
        provider = FakeProvider()
        LLMReviewEngine(provider).review(_CTX, _cfg(prompt_cache=False))
        systems = {c["messages"][0]["content"] for c in provider.calls}
        assert systems == {
            build_system_prompt(ReviewCategory.security),
            build_system_prompt(ReviewCategory.performance),
        }
        for call in provider.calls:
            assert [m["role"] for m in call["messages"]] == ["system", "user"]

    def test_intent_block_rides_the_lens_suffix_not_the_shared_prefix(self) -> None:
        ctx = _CTX.model_copy(update={"title": "Fix the frobnicator"})
        provider = FakeProvider()
        LLMReviewEngine(provider).review(
            ctx, _cfg(categories=[ReviewCategory.security, ReviewCategory.intent])
        )
        prefixes = {c["messages"][1]["content"] for c in provider.calls}
        assert len(prefixes) == 1  # intent must not fork the cached prefix
        assert not any("INTENT_START" in p for p in prefixes)
        intent_suffixes = [
            c["messages"][2]["content"]
            for c in provider.calls
            if "INTENT_START" in c["messages"][2]["content"]
        ]
        assert len(intent_suffixes) == 1
        assert "Fix the frobnicator" in intent_suffixes[0]

    def test_language_directive_rides_the_shared_prefix(self) -> None:
        """A set language reaches every lens via the shared system preamble, and
        the (system, diff) prefix stays byte-identical across the fan-out so a
        caching provider still serves it once."""
        provider = FakeProvider()
        LLMReviewEngine(provider).review(_CTX, _cfg(language="Japanese"))
        systems = {c["messages"][0]["content"] for c in provider.calls}
        assert len(systems) == 1
        assert "Japanese" in next(iter(systems))
        prefixes = {
            (c["messages"][0]["content"], c["messages"][1]["content"]) for c in provider.calls
        }
        assert len(prefixes) == 1

    def test_custom_lens_rides_the_same_split_shape(self) -> None:
        provider = FakeProvider()
        LLMReviewEngine(provider).review(
            _CTX,
            _cfg(
                categories=[ReviewCategory.security],
                extra_lenses=[CustomLens(id="naming", instructions="Flag bad names.")],
            ),
        )
        custom = [c for c in provider.calls if "Flag bad names." in c["messages"][2]["content"]]
        assert len(custom) == 1
        assert custom[0]["messages"][0]["content"] == build_shared_preamble()


class TestDirectoryBlockPlacement:
    """A directory rule must not disturb the cacheable prefix contract.

    It varies per batch — exactly like the static-analysis hints block — so it
    joins the SAME single prefix user message, never a fourth message and never
    the cross-batch system preamble.
    """

    def test_shared_preamble_is_byte_identical_with_and_without_rules(self) -> None:
        plain = FakeProvider()
        LLMReviewEngine(plain).review(_CTX, _cfg())
        scoped = FakeProvider()
        LLMReviewEngine(scoped).review(
            _CTX,
            _cfg(directory_rules=[{"instructions": "Money code is strict."}]),
        )
        assert {c["messages"][0]["content"] for c in plain.calls} == {
            c["messages"][0]["content"] for c in scoped.calls
        }
        assert {c["messages"][0]["content"] for c in scoped.calls} == {build_shared_preamble()}

    def test_the_block_rides_the_prefix_not_the_lens_block(self) -> None:
        provider = FakeProvider()
        LLMReviewEngine(provider).review(
            _CTX,
            _cfg(directory_rules=[{"instructions": "Money code is strict."}]),
        )
        for call in provider.calls:
            messages = call["messages"]
            assert [m["role"] for m in messages] == ["system", "user", "user"]
            assert "Money code is strict." in messages[1]["content"]
            assert "Money code is strict." not in messages[2]["content"]

    def test_the_prefix_stays_identical_across_lenses(self) -> None:
        """Still one cache entry per batch — the block is lens-independent."""
        provider = FakeProvider()
        LLMReviewEngine(provider).review(
            _CTX,
            _cfg(directory_rules=[{"instructions": "Money code is strict."}]),
        )
        assert len({c["messages"][1]["content"] for c in provider.calls}) == 1


class _EventOrderProvider(FakeProvider):
    """Records call start/end events so warm-up serialisation is observable."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.events: list[str] = []
        self._n = 0

    def complete(self, messages, model, **opts):  # type: ignore[override]
        with self._lock:
            i = self._n
            self._n += 1
            self.events.append(f"start-{i}")
        # Long enough that concurrent submissions would interleave their starts.
        threading.Event().wait(0.05)
        with self._lock:
            self.events.append(f"end-{i}")
        return ProviderResult(text='{"findings": []}', input_tokens=1, output_tokens=1)


def _big_ctx() -> PRContext:
    """A diff comfortably above the warm-up token floor (2048 tokens)."""
    lines = "".join(f"+changed_line_number_{i} = {i}\n" for i in range(900))
    diff = f"diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n@@ -1,1 +1,900 @@\n{lines}"
    assert count_tokens(diff) >= 2048
    return PRContext(
        diff=diff,
        changed_files=["big.py"],
        base_sha="a",
        head_sha="b",
        repo="org/repo",
        pr_number=1,
    )


class TestCacheWarmup:
    def test_primer_completes_before_the_rest_dispatch_on_breakpoint_routes(self) -> None:
        provider = _EventOrderProvider()
        cfg = _cfg(
            provider=Provider.anthropic,
            categories=[ReviewCategory.security, ReviewCategory.performance, ReviewCategory.tests],
        )
        LLMReviewEngine(provider).review(_big_ctx(), cfg)
        # The primer's end must precede every other call's start.
        assert provider.events[0] == "start-0"
        assert provider.events[1] == "end-0"

    def test_no_warmup_on_a_small_diff(self) -> None:
        """Below the token floor the primer costs more than it saves — the whole
        batch dispatches concurrently."""
        provider = _EventOrderProvider()
        cfg = _cfg(
            provider=Provider.anthropic,
            categories=[ReviewCategory.security, ReviewCategory.performance, ReviewCategory.tests],
        )
        LLMReviewEngine(provider).review(_CTX, cfg)
        starts_before_first_end = [
            e for e in provider.events[: provider.events.index("end-0")] if e.startswith("start")
        ]
        assert len(starts_before_first_end) >= 2

    @pytest.mark.parametrize(
        "provider_kind",
        [Provider.openai, Provider.azure, Provider.openrouter, Provider.zai, Provider.vertex],
    )
    def test_primer_warms_automatic_caching_providers_too(self, provider_kind: Provider) -> None:
        """The primer is not about the cache_control marker — it is about not
        letting a concurrent first wave all miss the shared prefix. Providers
        that cache automatically server-side (OpenAI, Azure, DeepSeek via
        openrouter) pay that same N-way miss, so they get warmed as well."""
        provider = _EventOrderProvider()
        cfg = _cfg(
            provider=provider_kind,
            categories=[ReviewCategory.security, ReviewCategory.performance, ReviewCategory.tests],
        )
        LLMReviewEngine(provider).review(_big_ctx(), cfg)
        assert provider.events[0] == "start-0"
        assert provider.events[1] == "end-0"

    def test_no_warmup_when_prompt_cache_is_off(self) -> None:
        """`prompt_cache: false` restores the legacy shape byte-for-byte, and
        that includes dispatching the batch fully concurrently."""
        provider = _EventOrderProvider()
        cfg = _cfg(
            provider=Provider.anthropic,
            prompt_cache=False,
            categories=[ReviewCategory.security, ReviewCategory.performance, ReviewCategory.tests],
        )
        LLMReviewEngine(provider).review(_big_ctx(), cfg)
        starts_before_first_end = [
            e for e in provider.events[: provider.events.index("end-0")] if e.startswith("start")
        ]
        assert len(starts_before_first_end) >= 2

    def test_failed_primer_still_releases_its_batch(self) -> None:
        calls = {"n": 0}
        lock = threading.Lock()

        class _FailingPrimer(FakeProvider):
            def complete(self, messages, model, **opts):  # type: ignore[override]
                with lock:
                    calls["n"] += 1
                    first = calls["n"] == 1
                if first:
                    raise RuntimeError("primer exploded")
                return ProviderResult(text='{"findings": []}', input_tokens=1, output_tokens=1)

        cfg = _cfg(
            provider=Provider.anthropic,
            categories=[ReviewCategory.security, ReviewCategory.performance, ReviewCategory.tests],
        )
        findings, summary = LLMReviewEngine(_FailingPrimer()).review(_big_ctx(), cfg)
        # Three lens calls — the two followers still ran, the primer's failure
        # never stranded them — plus the rescue wave's one more go at the primer,
        # which this time succeeds. So the round completes.
        assert calls["n"] == 4
        assert "review calls failed" not in summary


class TestReflectionSplitShape:
    def test_reflection_diff_rides_its_own_leading_user_block(self) -> None:
        import json

        finding = {
            "path": "a.py",
            "line": 1,
            "severity": "high",
            "title": "bug",
            "body": "broken",
            "failure_scenario": "When the changed line runs, the operation fails.",
        }

        class _Reviewer(FakeProvider):
            def complete(self, messages, model, **opts):  # type: ignore[override]
                self.calls.append({"messages": messages, "model": model, "opts": opts})
                if "auditing another reviewer" in messages[0]["content"]:
                    return ProviderResult(text='{"0": true}', input_tokens=1, output_tokens=1)
                return ProviderResult(text=json.dumps([finding]), input_tokens=1, output_tokens=1)

        provider = _Reviewer()
        LLMReviewEngine(provider).review(_CTX, _cfg(reflect=True))
        [reflect_call] = [
            c for c in provider.calls if "auditing another reviewer" in c["messages"][0]["content"]
        ]
        roles = [m["role"] for m in reflect_call["messages"]]
        assert roles == ["system", "user", "user"]
        assert reflect_call["messages"][1]["content"].startswith("Diff:\n")
        assert "Findings (indexed from 0)" in reflect_call["messages"][2]["content"]
