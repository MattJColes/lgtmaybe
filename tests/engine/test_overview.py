"""The change overview: one comment, three concurrent calls.

``build_overview`` composes the description, the High Impact Areas section and
the diagrams into the single body ``/diagram``, ``auto_diagram`` and the local
``lgtmaybe diagram`` all post or print. Contracts:

- the sections render in reading order: what the change is, what is risky about
  it, then the pictures;
- the description heads the comment, so the diagram's own title is suppressed;
- the description and high-impact sections are best-effort — either failing
  leaves a visible "unavailable" line and never blocks the rest;
- the diagram call keeps its failure semantics, because a required automatic
  diagram must be able to fail a run rather than silently complete it;
- with both sections off the body is byte-identical to the standalone diagram,
  and costs exactly one call.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from lgtmaybe.core.models import (
    DescribeResult,
    DiagramResult,
    HighImpactResult,
    PRContext,
    Provider,
    ProviderResult,
    ReviewConfig,
)
from lgtmaybe.core.ports import Message
from lgtmaybe.engine.diagram import build_diagram
from lgtmaybe.engine.overview import build_overview
from tests.fakes import FakeProvider

_CTX = PRContext(
    diff="diff --git a/infra/main.tf b/infra/main.tf\n@@ -1 +1,2 @@\n old\n+new\n",
    changed_files=["infra/main.tf"],
    base_sha="abc",
    head_sha="def",
    repo="org/repo",
    pr_number=8,
    title="Resize the cluster",
    description="Halves the node count.",
)

_NO_INTENT_CTX = _CTX.model_copy(update={"title": "", "description": "", "commit_messages": []})

_CFG = ReviewConfig(provider=Provider.ollama, model="llama3")

_DESCRIBE_PAYLOAD = {
    "title": "Halve the cluster node count",
    "change_type": "chore",
    "summary": "Drops the pool from eight nodes to four.",
    "walkthrough": [{"path": "infra/main.tf", "summary": "Sets node_count to four."}],
    "intent_check": "The diff does what the title says.",
}

_HIGH_IMPACT_PAYLOAD = {
    "areas": [
        {
            "area": "infrastructure",
            "title": "Cluster halved",
            "files": ["infra/main.tf"],
            "why": "Halving nodes removes peak headroom.",
            "check": "Confirm peak load fits four nodes.",
            "severity": "high",
        }
    ],
    "notes": "",
}

_DIAGRAM_PAYLOAD = {
    "title": "Cluster topology",
    "summary": "The node pool shrinks.",
    "nodes": [
        {"id": "lb", "label": "Load balancer", "change": "unchanged"},
        {"id": "pool", "label": "Node pool", "change": "changed"},
    ],
    "edges": [{"source": "lb", "target": "pool", "label": "routes"}],
    "steps": [{"source": "lb", "target": "pool", "label": "forwards request"}],
    "notes": "",
}


def _result(payload: dict[str, Any]) -> ProviderResult:
    return ProviderResult(text=json.dumps(payload), input_tokens=5, output_tokens=5)


def _provider(**overrides: ProviderResult) -> FakeProvider:
    results = {
        DescribeResult: _result(_DESCRIBE_PAYLOAD),
        HighImpactResult: _result(_HIGH_IMPACT_PAYLOAD),
        DiagramResult: _result(_DIAGRAM_PAYLOAD),
    }
    schemas = {"describe": DescribeResult, "high": HighImpactResult, "diagram": DiagramResult}
    for name, result in overrides.items():
        results[schemas[name]] = result
    return FakeProvider(results_by_schema=results)


def _schemas(provider: FakeProvider) -> set[Any]:
    """The schemas asked for. A set, never an index: the calls run concurrently."""
    return {call["opts"].get("response_format") for call in provider.calls}


class _FailingProvider:
    """Answers every schema but one, which raises."""

    def __init__(self, failing: type[Any]) -> None:
        self._failing = failing
        self._inner = _provider()

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        if opts.get("response_format") is self._failing:
            raise RuntimeError("provider down")
        return self._inner.complete(messages, model, **opts)


class TestLayout:
    def test_sections_render_in_reading_order(self) -> None:
        body = build_overview(_CTX, _CFG, _provider())

        order = [
            "## Halve the cluster node count",
            "**Change type:** chore",
            "### **High Impact Areas**",
            "### Walkthrough",
            "### Does it do what it says?",
            "### Structure",
            "### Sequence",
        ]
        positions = [body.index(section) for section in order]
        assert positions == sorted(positions), body

    def test_the_description_heads_the_comment_not_the_diagram_title(self) -> None:
        """One comment, one headline: the diagram's own title would be a second
        `##` competing with the description's."""
        body = build_overview(_CTX, _CFG, _provider())

        assert body.startswith("## Halve the cluster node count")
        assert "Cluster topology" not in body

    def test_the_intent_check_is_omitted_without_a_stated_intent(self) -> None:
        body = build_overview(_NO_INTENT_CTX, _CFG, _provider())

        assert "### Does it do what it says?" not in body
        assert "### **High Impact Areas**" in body

    def test_three_sections_are_three_calls(self) -> None:
        provider = _provider()

        build_overview(_CTX, _CFG, provider)

        assert _schemas(provider) == {DescribeResult, HighImpactResult, DiagramResult}

    def test_the_calls_run_concurrently(self) -> None:
        """Three sequential round-trips would triple the wall clock of every
        push; the barrier only clears if all three are in flight at once."""
        barrier = threading.Barrier(3, timeout=5)
        inner = _provider()

        class _Barrier:
            def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
                barrier.wait()
                return inner.complete(messages, model, **opts)

        build_overview(_CTX, _CFG, _Barrier())  # raises BrokenBarrierError if serial


class TestToggles:
    def test_both_sections_off_is_the_standalone_diagram(self) -> None:
        cfg = _CFG.model_copy(update={"auto_describe": False, "high_impact": False})
        provider, control = _provider(), _provider()

        body = build_overview(_CTX, cfg, provider)

        assert body == build_diagram(_CTX, cfg, control)
        assert _schemas(provider) == {DiagramResult}

    def test_without_a_description_the_diagram_header_returns(self) -> None:
        cfg = _CFG.model_copy(update={"auto_describe": False})

        body = build_overview(_CTX, cfg, _provider())

        assert body.startswith("## Cluster topology")
        assert body.index("### **High Impact Areas**") < body.index("### Structure")

    def test_high_impact_can_be_dropped_on_its_own(self) -> None:
        cfg = _CFG.model_copy(update={"high_impact": False})
        provider = _provider()

        body = build_overview(_CTX, cfg, provider)

        assert "### **High Impact Areas**" not in body
        assert _schemas(provider) == {DescribeResult, DiagramResult}


class TestDegradedSections:
    def test_an_unparseable_description_leaves_a_visible_line(self) -> None:
        provider = _provider(describe=ProviderResult(text="prose", input_tokens=1, output_tokens=1))

        body = build_overview(_CTX, _CFG, provider)

        assert "Description unavailable" in body
        assert "### **High Impact Areas**" in body
        assert "### Structure" in body

    def test_a_failing_describe_call_does_not_block_the_overview(self) -> None:
        body = build_overview(_CTX, _CFG, _FailingProvider(DescribeResult))

        assert "Description unavailable" in body
        assert "### Structure" in body

    def test_a_failing_high_impact_call_still_shows_the_path_floor(self) -> None:
        body = build_overview(_CTX, _CFG, _FailingProvider(HighImpactResult))

        assert "### **High Impact Areas**" in body
        assert "`infra/main.tf`" in body
        assert "assessment unavailable" in body
        assert "### Structure" in body

    def test_a_failing_diagram_call_fails_the_overview(self) -> None:
        """A required automatic diagram must be able to fail a run: swallowing
        this would stamp the head complete with no diagram on it."""
        with pytest.raises(RuntimeError):
            build_overview(_CTX, _CFG, _FailingProvider(DiagramResult))

    def test_an_invalid_diagram_keeps_the_other_sections(self) -> None:
        provider = _provider(
            diagram=ProviderResult(
                text='{"title": "t", "nodes": []}', input_tokens=1, output_tokens=1
            )
        )

        body = build_overview(_CTX, _CFG, provider)

        assert "## Halve the cluster node count" in body
        assert "### **High Impact Areas**" in body
        assert "couldn't produce a valid change diagram" in body
        assert "## Architecture of this change" not in body
