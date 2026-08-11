"""Tests for the provider-aware fast/full review presets."""

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
from tests.conftest import make_cfg
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


class TestFastLensGrouping:
    def test_default_preset_is_fast(self) -> None:
        assert make_cfg().preset is ReviewPreset.fast

    _FOUR = ["security", "correctness", "code-health", "artefacts"]

    @pytest.mark.parametrize(
        "overrides",
        [
            {},  # ollama: single-worker auto-concurrency
            {"provider": Provider.openai, "max_concurrency": None},  # cloud auto
            {"provider": Provider.openai, "max_concurrency": 1},  # forced serial
            {"max_concurrency": 2},  # local provider, forced parallel
        ],
        ids=["ollama-auto", "cloud-auto", "cloud-serial", "local-parallel"],
    )
    def test_fast_builds_the_same_four_lenses_on_every_configuration(
        self, overrides: dict[str, object]
    ) -> None:
        """The lens set is a property of the preset, not of how many workers
        happen to be available: one concern per call, four calls, everywhere."""
        lenses = _build_lenses(make_cfg(**overrides), has_intent=False)
        assert [lens.id for lens in lenses] == self._FOUR

    def test_fast_covers_every_built_in_category(self) -> None:
        """Four distinct lenses cover the nine everyday categories — nothing that
        used to be reviewed silently stops being reviewed. Spec is the tenth and
        the exception: it has its own call, and only when a spec matches the PR,
        so it is passed here as present for the coverage claim to mean anything."""
        lenses = _build_lenses(make_cfg(), has_intent=True, has_spec=True)
        covered: set[str] = set()
        for lens in lenses:
            covered.add(lens.id)
            covered |= set(lens.allowed_categories or ())
        assert covered >= {c.value for c in ReviewCategory}

    def test_merged_prompts_name_their_member_categories(self) -> None:
        lenses = {lens.id: lens for lens in _build_lenses(make_cfg(), has_intent=False)}
        code_health = lenses["code-health"].user_block
        for name in ("performance", "complexity", "ponytail", "deprecation"):
            assert f'"{name}"' in code_health
        artefacts = lenses["artefacts"].user_block
        for name in ("tests", "documentation"):
            assert f'"{name}"' in artefacts

    def test_intent_folds_into_combined_correctness_when_stated(self) -> None:
        lenses = {lens.id: lens for lens in _build_lenses(make_cfg(), has_intent=True)}
        correctness = lenses["correctness"]
        assert correctness.carries_intent
        assert correctness.allowed_categories == frozenset({"correctness", "intent"})
        assert "stated intent" in correctness.user_block
        # No stated intent → plain correctness call, no intent rubric.
        plain = {lens.id: lens for lens in _build_lenses(make_cfg(), has_intent=False)}
        assert not plain["correctness"].carries_intent
        assert "stated intent" not in plain["correctness"].user_block

    def test_full_preset_builds_one_lens_per_category(self) -> None:
        lenses = _build_lenses(make_cfg(preset="full"), has_intent=True, has_spec=True)
        assert [lens.id for lens in lenses] == [c.value for c in ReviewCategory]
        assert {"tests", "documentation"} <= {lens.id for lens in lenses}

    def test_full_preset_skips_intent_without_a_stated_intent(self) -> None:
        lenses = _build_lenses(make_cfg(preset="full"), has_intent=False)
        assert "intent" not in [lens.id for lens in lenses]

    def test_explicit_categories_override_the_fast_grouping(self) -> None:
        cfg = make_cfg(categories=[ReviewCategory.security, ReviewCategory.performance])
        lenses = _build_lenses(cfg, has_intent=False)
        assert [lens.id for lens in lenses] == ["security", "performance"]

    @pytest.mark.parametrize("provider", [Provider.ollama, Provider.openai])
    def test_fast_review_makes_four_calls_on_any_provider(self, provider: Provider) -> None:
        """Worker count changes how the four calls are scheduled, never how many
        there are — a single-slot provider runs the same four, serially."""
        fake = FakeProvider()
        LLMReviewEngine(fake).review(_CTX, make_cfg(provider=provider))
        assert len(fake.calls) == 4


class TestMergedCategoryStamping:
    def _finding(self, category: str | None) -> str:
        f = ReviewFinding(
            path="a.py",
            line=1,
            severity=Severity.medium,
            title="finding",
            body="x",
            failure_scenario="When the changed line runs, the operation fails.",
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

        cfg = make_cfg(min_severity="info")
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


def test_lenses_narrow_dependency_health_when_a_scanner_covers_it() -> None:
    """Config-derived, not environment-derived.

    Deciding this from `shutil.which` would make the prompt — and so the shared
    prefix cache — depend on what happens to be installed on the machine. A
    configured-but-missing scanner warns instead.
    """
    from lgtmaybe.core.models import (
        Provider,
        ReviewConfig,
        StaticAnalysisTool,
        ToolMode,
    )
    from lgtmaybe.engine.engine import _build_lenses

    def _lens_text(cfg: ReviewConfig) -> str:
        return " ".join(lens.user_block for lens in _build_lenses(cfg, has_intent=False))

    off = ReviewConfig(provider=Provider.ollama, model="llama3")
    assert "known-vulnerable" in _lens_text(off)

    sa = off.static_analysis.model_copy(
        update={"enabled": True, "tools": [StaticAnalysisTool.osv_scanner]}
    )
    on = off.model_copy(update={"static_analysis": sa})
    assert "known-vulnerable" not in _lens_text(on)

    # Demoted to a hint, the model is back to being the only reporter.
    hinted = on.model_copy(
        update={
            "static_analysis": sa.model_copy(
                update={"tool_mode": {StaticAnalysisTool.osv_scanner: ToolMode.hint}}
            )
        }
    )
    assert "known-vulnerable" in _lens_text(hinted)
