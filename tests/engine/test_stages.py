"""Each preparation stage, called on its own (see ``engine/stages.py``).

The engine-level suites cover the stages through a whole review; these pin
each stage's own inputs and outputs, so a stage can be reordered or reused
without a review around it.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from lgtmaybe.core.models import (
    PRContext,
    PRStack,
    ReviewCategory,
    Severity,
    StackLayer,
    StaticAnalysisConfig,
    StaticAnalysisTool,
    ToolMode,
)
from lgtmaybe.engine import stages
from lgtmaybe.engine.lenses import _build_lenses, _Lens
from lgtmaybe.engine.redact import REDACTED_PLACEHOLDER
from lgtmaybe.engine.static_analysis import ToolFinding
from tests.conftest import make_cfg


def _patch(path: str, added: int = 1) -> str:
    body = "".join(f"+line {i}\n" for i in range(added))
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,0 +1,{added} @@\n{body}"
    )


def _ctx(**overrides: Any) -> PRContext:
    base: dict[str, Any] = {
        "diff": _patch("a.py"),
        "changed_files": ["a.py"],
        "base_sha": "b",
        "head_sha": "h",
        "repo": "o/r",
        "pr_number": 1,
    }
    base.update(overrides)
    return PRContext(**base)


def _hint(path: str, tool: str = "bandit") -> ToolFinding:
    return ToolFinding(
        tool=tool, path=path, line=1, rule="B1", message="danger", severity=Severity.medium
    )


class TestLensCeilings:
    def test_both_off_when_unset(self) -> None:
        assert stages.lens_ceilings(make_cfg(max_review_seconds=0, max_review_tokens=0)) == (
            None,
            None,
        )

    def test_a_tenth_is_held_back_for_the_auditor(self) -> None:
        before = time.perf_counter()
        deadline_at, budget_at = stages.lens_ceilings(
            make_cfg(max_review_seconds=100, max_review_tokens=1_000)
        )
        assert deadline_at is not None
        assert before + 90 <= deadline_at <= time.perf_counter() + 90
        assert budget_at == 900

    def test_the_time_reserve_is_capped(self) -> None:
        before = time.perf_counter()
        deadline_at, _ = stages.lens_ceilings(make_cfg(max_review_seconds=10_000))
        assert deadline_at is not None
        assert deadline_at >= before + 10_000 - 300


class TestRedactInputs:
    def test_the_diff_and_the_intent_are_both_redacted(self) -> None:
        secret = "AKIAIOSFODNN7EXAMPLE"
        ctx = _ctx(diff=_patch("a.py") + f"+key = '{secret}'\n", title=f"Rotate {secret}")
        redacted = stages.redact_inputs(ctx)
        assert secret not in redacted.diff
        assert REDACTED_PLACEHOLDER in redacted.diff
        assert redacted.intent is not None
        assert redacted.intent.startswith("Title: Rotate")
        assert secret not in redacted.intent

    def test_no_stated_intent_is_none(self) -> None:
        assert stages.redact_inputs(_ctx()).intent is None

    def test_every_stack_field_is_redacted_before_egress(self) -> None:
        """The stack rides the shared prefix of every call, so each title, path
        and ref in it leaves the process like the diff does."""
        secret = "AKIAIOSFODNN7EXAMPLE"
        stack = PRStack(
            position=2,
            size=3,
            base_ref=f"release/{secret}",
            below=[StackLayer(number=10, title=f"Below {secret}", files=[f"below/{secret}.py"])],
            above=[StackLayer(number=12, title=f"Above {secret}", files=[f"above/{secret}.py"])],
        )

        clean = stages._redacted_stack(stack)

        assert clean is not None
        assert secret not in clean.model_dump_json()
        assert clean.base_ref.startswith("release/")
        assert [layer.number for layer in clean.below + clean.above] == [10, 12]
        assert stages._redacted_stack(None) is None


def test_spec_context_is_skipped_when_spec_review_is_off(tmp_path: Path) -> None:
    spec = stages.spec_context(make_cfg(spec_review=False), _ctx(), tmp_path)
    assert spec.text is None
    assert spec.unfit == ()


class TestRetrievalBudget:
    def test_off_by_default(self) -> None:
        assert stages.retrieval_budget(make_cfg(), can_fetch=True) is None

    def test_needs_a_reader(self) -> None:
        cfg = make_cfg(mid_review_retrieval=True)
        assert stages.retrieval_budget(cfg, can_fetch=False) is None

    def test_a_quarter_of_the_input_budget(self) -> None:
        cfg = make_cfg(mid_review_retrieval=True, max_input_tokens=40_000)
        assert stages.retrieval_budget(cfg, can_fetch=True) == 10_000


def test_filter_files_drops_noise_excluded_paths_and_oversized_patches() -> None:
    diff = _patch("a.py") + _patch("uv.lock") + _patch("docs/x.md") + _patch("big.py", 50)
    ctx = _ctx(diff=diff, changed_files=["a.py", "uv.lock", "docs/x.md", "big.py"])
    filtered = stages.filter_files(
        diff, ctx, make_cfg(exclude_paths=["docs/**"], max_file_diff_lines=20)
    )
    assert [path for path, _ in filtered.patches] == ["a.py"]
    assert filtered.oversized == ["big.py"]


class TestScanFiles:
    def test_disabled_never_touches_the_runner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def forbidden(files: dict[str, str], cfg: object) -> list[ToolFinding]:
            raise AssertionError("the runner ran with static analysis off")

        monkeypatch.setattr(stages, "run_static_analysis", forbidden)
        ctx = _ctx(file_contents={"a.py": "x"})
        cfg = make_cfg(static_analysis=StaticAnalysisConfig(enabled=False))
        assert stages.scan_files([("a.py", _patch("a.py"))], ctx, cfg) == ([], [], [], "")

    def test_scans_reviewed_files_and_scan_only_manifests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[dict[str, str]] = []
        found = [_hint("a.py"), _hint("a.py", tool="gitleaks")]

        def runner(files: dict[str, str], cfg: object) -> list[ToolFinding]:
            seen.append(files)
            return found

        monkeypatch.setattr(stages, "run_static_analysis", runner)
        ctx = _ctx(
            file_contents={"a.py": "x = 1\n", "unreviewed.py": "y"},
            scan_contents={"uv.lock": "lock"},
        )
        cfg = make_cfg(
            static_analysis=StaticAnalysisConfig(
                tool_mode={StaticAnalysisTool.gitleaks: ToolMode.finding}
            )
        )
        patches = [("a.py", _patch("a.py"))]
        scan = stages.scan_files(patches, ctx, cfg)
        assert seen == [{"a.py": "x = 1\n", "uv.lock": "lock"}]
        assert scan.tool_findings == found
        assert [h.tool for h in scan.hints] == ["bandit"]
        assert len(scan.findings) == 1
        assert scan.scope == _patch("a.py")


class TestTriagePatches:
    def test_off_keeps_every_file_without_a_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def forbidden(*args: object) -> object:
            raise AssertionError("triage ran with no triage_model")

        monkeypatch.setattr(stages, "triage_files", forbidden)
        patches = [("a.py", "p")]
        assert stages.triage_patches(patches, [], make_cfg(), object()) == (patches, [])  # type: ignore[arg-type]

    def test_on_hands_over_to_triage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hits = [_hint("a.py")]

        def triage(patches: object, tool_findings: object, cfg: object, provider: object) -> object:
            assert tool_findings is hits
            return [("a.py", "p")], ["b.py"]

        monkeypatch.setattr(stages, "triage_files", triage)
        result = stages.triage_patches(
            [("a.py", "p"), ("b.py", "q")],
            hits,
            make_cfg(triage_model="cheap"),
            object(),  # type: ignore[arg-type]
        )
        assert result == ([("a.py", "p")], ["b.py"])


def test_cap_files_keeps_the_first_max_files() -> None:
    patches = [("a.py", "1"), ("b.py", "2"), ("c.py", "3")]
    assert stages.cap_files(patches, make_cfg(max_files=2)) == (patches[:2], True)
    assert stages.cap_files(patches, make_cfg(max_files=3)) == (patches, False)


class TestExpandContext:
    _HEAD = "".join(f"line {i}\n" for i in range(1, 41))
    _PATCH = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -20,1 +20,1 @@\n-old 20\n+line 20\n"
    )

    def test_pads_hunks_from_the_head_text(self) -> None:
        ctx = _ctx(file_contents={"a.py": self._HEAD})
        cfg = make_cfg(context_lines=3, function_context=False)
        [(path, expanded)] = stages.expand_context([("a.py", self._PATCH)], ctx, cfg)
        assert path == "a.py"
        assert " line 19\n" in expanded
        assert len(expanded) > len(self._PATCH)

    def test_zero_context_lines_leaves_patches_alone(self) -> None:
        ctx = _ctx(file_contents={"a.py": self._HEAD})
        patches = [("a.py", self._PATCH)]
        assert stages.expand_context(patches, ctx, make_cfg(context_lines=0)) == patches


def test_batch_patches_splits_to_the_budget() -> None:
    cfg = make_cfg(max_input_tokens=6_000)
    lenses = _build_lenses(cfg, has_intent=False)
    patches = [(f"f{i}.py", _patch(f"f{i}.py", 600)) for i in range(4)]
    batches = stages.batch_patches(patches, lenses, cfg)
    assert len(batches) > 1
    assert [p for batch in batches for p in batch] == patches


def test_directory_context_reads_nothing_without_rules(tmp_path: Path) -> None:
    assert stages.directory_context(make_cfg(), tmp_path) == {}


class TestCarryUnbatchedHints:
    _LENSES = [
        _Lens(id=ReviewCategory.correctness.value, user_block="correctness"),
        _Lens(id=ReviewCategory.security.value, user_block="security"),
    ]

    def test_the_security_lens_carries_scan_only_hints(self) -> None:
        lock, batched = _hint("uv.lock"), _hint("a.py")
        carried = stages.carry_unbatched_hints(
            [lock, batched], {"uv.lock": "x"}, [[("a.py", "p")]], self._LENSES
        )
        assert carried.hints == [lock]
        assert carried.carrier is self._LENSES[1]
        assert carried.carrier_lens is not None
        assert carried.carrier_lens.user_block.startswith(stages._UNBATCHED_HINTS_LEAD)
        assert carried.carrier_lens.user_block.endswith("\n\nsecurity")
        assert carried.dropped == 0

    def test_no_carrier_lens_drops_and_counts_them(self) -> None:
        other = [_Lens(id="docs", user_block="docs")]
        carried = stages.carry_unbatched_hints(
            [_hint("uv.lock")], {"uv.lock": "x"}, [[("a.py", "p")]], other
        )
        assert (carried.carrier, carried.carrier_lens, carried.dropped) == (None, None, 1)


class TestBlocksBuilder:
    def test_blocks_name_what_the_paths_do_not_show(self) -> None:
        ctx = _ctx(changed_files=["a.py", "b.py"])
        blocks_for = stages.blocks_builder(ctx, make_cfg(), [_hint("b.py")], "Title: x", None, {})
        intent, hints, directory, spec = blocks_for({"a.py"})
        assert intent is not None
        assert "b.py" in intent
        assert hints is None  # the only hint is on a file these paths do not show
        assert directory is None
        assert spec is None

        _, hints_b, _, _ = blocks_for({"b.py"})
        assert hints_b is not None
        assert "danger" in hints_b

    def test_no_intent_no_intent_block(self) -> None:
        blocks_for = stages.blocks_builder(_ctx(), make_cfg(), [], None, None, {})
        assert blocks_for({"a.py"}) == (None, None, None, None)
