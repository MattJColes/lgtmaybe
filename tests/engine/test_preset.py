"""Tests for the fast/full review presets (fast = 4 grouped calls, default)."""

from __future__ import annotations

import json

import pytest

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewCategory,
    ReviewConfig,
    ReviewFinding,
    ReviewPreset,
    Severity,
)
from lgtmaybe.engine import LLMReviewEngine
from lgtmaybe.engine.engine import _build_lenses
from lgtmaybe.providers.factory import cheaper_reflect_sibling
from tests.fakes import FakeProvider

_CTX = PRContext(
    diff="@@ -1,3 +1,4 @@\n context\n+new line\n context\n",
    changed_files=["a.py"],
    base_sha="abc",
    head_sha="def",
    repo="org/repo",
    pr_number=1,
)
_CTX_WITH_INTENT = _CTX.model_copy(update={"title": "Fix pagination"})


def _cfg(**overrides: object) -> ReviewConfig:
    defaults: dict[str, object] = {"provider": Provider.ollama, "model": "m", "reflect": False}
    defaults.update(overrides)
    return ReviewConfig(**defaults)  # type: ignore[arg-type]


class TestFastLensGrouping:
    def test_default_preset_is_fast(self) -> None:
        assert _cfg().preset is ReviewPreset.fast

    def test_fast_builds_four_lenses(self) -> None:
        lenses = _build_lenses(_cfg(), has_intent=False)
        assert [lens.id for lens in lenses] == [
            "security",
            "correctness",
            "code-health",
            "artefacts",
        ]

    def test_fast_covers_every_builtin_category(self) -> None:
        """Four calls, nine lenses: every category is either a dedicated call
        or a member of a merged one — nothing silently dropped."""
        lenses = _build_lenses(_cfg(), has_intent=True)
        covered: set[str] = set()
        for lens in lenses:
            covered.add(lens.id)
            covered |= set(lens.allowed_categories or ())
        assert covered >= {c.value for c in ReviewCategory}

    def test_merged_prompts_name_their_member_categories(self) -> None:
        lenses = {lens.id: lens for lens in _build_lenses(_cfg(), has_intent=False)}
        code_health = lenses["code-health"].user_block
        for name in ("performance", "complexity", "ponytail", "deprecation"):
            assert f'"{name}"' in code_health
        artefacts = lenses["artefacts"].user_block
        assert '"tests"' in artefacts and '"documentation"' in artefacts

    def test_intent_folds_into_correctness_when_stated(self) -> None:
        lenses = {lens.id: lens for lens in _build_lenses(_cfg(), has_intent=True)}
        correctness = lenses["correctness"]
        assert correctness.carries_intent
        assert correctness.allowed_categories == frozenset({"correctness", "intent"})
        assert "stated intent" in correctness.user_block
        # No stated intent → plain correctness call, no intent rubric.
        plain = {lens.id: lens for lens in _build_lenses(_cfg(), has_intent=False)}
        assert not plain["correctness"].carries_intent
        assert "stated intent" not in plain["correctness"].user_block

    def test_full_preset_builds_one_lens_per_category(self) -> None:
        lenses = _build_lenses(_cfg(preset="full"), has_intent=True)
        assert [lens.id for lens in lenses] == [c.value for c in ReviewCategory]

    def test_full_preset_skips_intent_without_a_stated_intent(self) -> None:
        lenses = _build_lenses(_cfg(preset="full"), has_intent=False)
        assert "intent" not in [lens.id for lens in lenses]

    def test_explicit_categories_override_the_fast_grouping(self) -> None:
        cfg = _cfg(categories=[ReviewCategory.security, ReviewCategory.performance])
        lenses = _build_lenses(cfg, has_intent=False)
        assert [lens.id for lens in lenses] == ["security", "performance"]

    def test_fast_review_makes_four_calls(self) -> None:
        provider = FakeProvider()
        LLMReviewEngine(provider).review(_CTX, _cfg())
        assert len(provider.calls) == 4


class TestMergedCategoryStamping:
    def _finding(self, category: str | None) -> str:
        f = ReviewFinding(
            path="a.py",
            line=1,
            severity=Severity.medium,
            title="finding",
            body="x",
            category=category,
        )
        return json.dumps([f.model_dump(mode="json")])

    def _run(self, model_category: str | None) -> list[ReviewFinding]:
        finding_text = self._finding(model_category)

        class _CodeHealthOnly(FakeProvider):
            """Only the code-health call yields the finding, so dedupe can't
            hide which lens's stamping produced the survivor."""

            def complete(self, messages, model, **opts):  # type: ignore[override]
                self.calls.append({"messages": messages, "model": model, "opts": opts})
                prompt = "\n".join(str(m.get("content", "")) for m in messages)
                if "Code health" in prompt:
                    return ProviderResult(text=finding_text, input_tokens=1, output_tokens=1)
                return ProviderResult(text='{"findings": []}', input_tokens=1, output_tokens=1)

        cfg = _cfg(min_severity="info")
        findings, _ = LLMReviewEngine(_CodeHealthOnly()).review(_CTX, cfg)
        return findings

    def test_member_category_from_the_model_is_kept(self) -> None:
        [finding] = self._run("performance")
        assert finding.category == "performance"

    def test_bogus_model_category_falls_back_to_the_lens_id(self) -> None:
        [finding] = self._run("made-up-lens")
        assert finding.category == "code-health"

    def test_missing_model_category_falls_back_to_the_lens_id(self) -> None:
        [finding] = self._run(None)
        assert finding.category == "code-health"


class TestCheaperReflectSibling:
    def test_anthropic_sonnet_and_opus_map_to_haiku(self) -> None:
        assert cheaper_reflect_sibling(Provider.anthropic, "claude-sonnet-4-6") == (
            "claude-haiku-4-5"
        )
        assert cheaper_reflect_sibling(Provider.anthropic, "claude-opus-4-8") == "claude-haiku-4-5"

    def test_openai_gpt5_maps_to_mini(self) -> None:
        assert cheaper_reflect_sibling(Provider.openai, "gpt-5.5") == "gpt-5-mini"

    def test_already_small_models_do_not_map(self) -> None:
        assert cheaper_reflect_sibling(Provider.anthropic, "claude-haiku-4-5") is None
        assert cheaper_reflect_sibling(Provider.openai, "gpt-5-mini") is None
        assert cheaper_reflect_sibling(Provider.openai, "gpt-5-nano") is None

    @pytest.mark.parametrize(
        "provider",
        [p for p in Provider if p not in (Provider.anthropic, Provider.openai)],
    )
    def test_other_providers_never_map(self, provider: Provider) -> None:
        """Model-id schemes elsewhere (bedrock/vertex regions and versions,
        user-pulled ollama tags) drift — a wrong guess 404s every reflection
        pass, so those providers keep reflecting with the review model."""
        assert cheaper_reflect_sibling(provider, "claude-sonnet-4-6") is None
