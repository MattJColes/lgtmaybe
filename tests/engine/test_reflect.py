"""Tests for reflect.py — self-reflection / false-positive filter."""

from __future__ import annotations

import json

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReflectionResult,
    ReviewConfig,
    ReviewFinding,
    Severity,
)
from lgtmaybe.engine.reflect import reflect_findings
from tests.fakes import FakeProvider

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

_CTX = PRContext(
    diff="@@ -1,3 +1,4 @@\n context\n+new line\n context\n",
    changed_files=["a.py"],
    base_sha="abc",
    head_sha="def",
    repo="org/repo",
    pr_number=1,
)

_CFG = ReviewConfig(provider=Provider.ollama, model="llama3")

_HIGH = ReviewFinding(
    path="a.py", line=1, severity=Severity.high, title="real bug", body="definitely broken"
)
_LOW_CONF = ReviewFinding(
    path="a.py", line=2, severity=Severity.low, title="dubious", body="probably fine"
)


def _reflection_result(verdicts: dict[int, bool]) -> str:
    """Build the JSON the reflection pass returns: {index: keep_bool}."""
    return json.dumps(verdicts)


def _fake_with_verdict(verdicts: dict[int, bool]) -> FakeProvider:
    text = _reflection_result(verdicts)
    return FakeProvider(result=ProviderResult(text=text, input_tokens=5, output_tokens=5))


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_low_confidence_finding_dropped() -> None:
    # Reflection pass says: keep finding 0 (high), drop finding 1 (low-conf)
    provider = _fake_with_verdict({0: True, 1: False})

    survivors = reflect_findings([_HIGH, _LOW_CONF], _CTX, _CFG, provider)

    assert _HIGH in survivors
    assert _LOW_CONF not in survivors


def test_high_confidence_finding_survives() -> None:
    provider = _fake_with_verdict({0: True})

    survivors = reflect_findings([_HIGH], _CTX, _CFG, provider)

    assert _HIGH in survivors


def test_empty_findings_returns_empty() -> None:
    provider = FakeProvider(result=ProviderResult(text="{}", input_tokens=1, output_tokens=1))
    survivors = reflect_findings([], _CTX, _CFG, provider)
    assert survivors == []


def test_reflect_calls_provider_once() -> None:
    provider = _fake_with_verdict({0: True, 1: False})

    reflect_findings([_HIGH, _LOW_CONF], _CTX, _CFG, provider)

    assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# structured-output verdict envelope
# ---------------------------------------------------------------------------


def _envelope(verdicts: list[tuple[int, bool]]) -> str:
    return json.dumps({"verdicts": [{"index": i, "keep": k} for i, k in verdicts]})


def _fake_with_text(text: str) -> FakeProvider:
    return FakeProvider(result=ProviderResult(text=text, input_tokens=5, output_tokens=5))


def test_structured_verdict_envelope_drops_low_confidence() -> None:
    provider = _fake_with_text(_envelope([(0, True), (1, False)]))

    survivors = reflect_findings([_HIGH, _LOW_CONF], _CTX, _CFG, provider)

    assert _HIGH in survivors
    assert _LOW_CONF not in survivors


def test_reflection_passes_response_format_when_structured() -> None:
    provider = _fake_with_verdict({0: True})  # _CFG has structured_output=True (default)

    reflect_findings([_HIGH], _CTX, _CFG, provider)

    assert provider.calls[0]["opts"].get("response_format") is ReflectionResult


def test_reflection_omits_response_format_when_disabled() -> None:
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", structured_output=False)
    provider = _fake_with_verdict({0: True})

    reflect_findings([_HIGH], _CTX, cfg, provider)

    assert "response_format" not in provider.calls[0]["opts"]


def test_verdict_with_think_block_and_fence_parses() -> None:
    text = "<think>let me judge</think>\n```json\n" + _envelope([(0, False)]) + "\n```"
    provider = _fake_with_text(text)

    survivors = reflect_findings([_HIGH], _CTX, _CFG, provider)

    assert survivors == []  # the verdict (keep=false) was parsed through the noise


def test_reflect_prompt_names_gap_findings_as_valid_types() -> None:
    """The keep-criterion must not read as "only bugs in the changed line count":
    a literal-minded judge would otherwise systematically prune missing-test,
    missing-doc, performance, and intent-mismatch findings."""
    provider = _fake_with_verdict({0: True})

    reflect_findings([_HIGH], _CTX, _CFG, provider)

    system = provider.calls[0]["messages"][0]["content"].lower()
    assert "missing test" in system or "missing-test" in system
    assert "intent" in system


def test_reflect_prompt_drops_unseen_code_assumptions() -> None:
    """The auditor must drop findings whose validity hinges on an assumption about
    code not shown in the diff (e.g. a guard/field that may exist elsewhere) — the
    exact shape of the cross-file false positives we keep seeing."""
    provider = _fake_with_verdict({0: True})

    reflect_findings([_HIGH], _CTX, _CFG, provider)

    system = provider.calls[0]["messages"][0]["content"].lower()
    assert "cannot see" in system or "not shown" in system
    assert "missing" in system


def test_reflect_prompt_drops_narration_only_findings() -> None:
    """The auditor must drop findings that merely describe the change without
    naming a concrete problem — the INFO-level narration a weak model emits."""
    provider = _fake_with_verdict({0: True})

    reflect_findings([_HIGH], _CTX, _CFG, provider)

    system = provider.calls[0]["messages"][0]["content"].lower()
    assert "narrat" in system or "describe" in system


def test_unparseable_verdict_keeps_all() -> None:
    provider = _fake_with_text("I'm not really sure about these.")

    survivors = reflect_findings([_HIGH, _LOW_CONF], _CTX, _CFG, provider)

    assert survivors == [_HIGH, _LOW_CONF]  # safe default


# ---------------------------------------------------------------------------
# gateway output without JSON mode (issue #104): prose-wrapped verdicts
# ---------------------------------------------------------------------------


def test_verdict_wrapped_in_conversational_prose_parses() -> None:
    """A gateway that ignores response_format returns the verdict inside prose;
    reflection must still parse it, not silently keep everything."""
    text = "Sure, here are my verdicts:\n" + _envelope([(0, True), (1, False)]) + "\nDone."
    provider = _fake_with_text(text)

    survivors = reflect_findings([_HIGH, _LOW_CONF], _CTX, _CFG, provider)

    assert _HIGH in survivors
    assert _LOW_CONF not in survivors


def test_verdict_after_bracket_bearing_prose_parses() -> None:
    """Prose with stray brackets before the JSON must not derail extraction."""
    text = "I checked findings [0, 1] carefully:\n" + _envelope([(0, False)])
    provider = _fake_with_text(text)

    survivors = reflect_findings([_HIGH], _CTX, _CFG, provider)

    assert survivors == []  # the keep=false verdict was parsed past the prose


# ---------------------------------------------------------------------------
# PIECE 1 — grounding reflection with file head text (asymmetric context)
# ---------------------------------------------------------------------------


def test_grounding_includes_head_text_of_flagged_file() -> None:
    """The full head text of a file carrying a finding reaches the auditor so it
    can verify a whole-file claim (e.g. that an import IS present)."""
    ctx = _CTX.model_copy(
        update={"file_contents": {"a.py": "import os\nimport sys\n\ndef f():\n    return 1\n"}}
    )
    provider = _fake_with_verdict({0: True})

    reflect_findings([_HIGH], ctx, _CFG, provider)

    user = provider.calls[0]["messages"][1]["content"]
    assert "import sys" in user  # the file head text was attached
    assert "def f():" in user


def test_grounding_redacts_secret_in_file_contents() -> None:
    """PRContext.file_contents is RAW head text — a secret in it must be redacted
    before the grounding block is sent to the provider."""
    secret = "AKIA" + "A" * 16
    ctx = _CTX.model_copy(
        update={"file_contents": {"a.py": f"key = '{secret}'\n"}}
    )
    provider = _fake_with_verdict({0: True})

    reflect_findings([_HIGH], ctx, _CFG, provider)

    user = provider.calls[0]["messages"][1]["content"]
    assert secret not in user
    assert "[REDACTED]" in user


def test_reflect_prompt_has_new_grounding_drop_rules() -> None:
    """The three new drop-rules' keywords appear in the system prompt: a
    library/framework-semantics claim the diff doesn't prove, a missing-import/
    await/symbol claim contradicted by the provided file text, and a
    test-will-fail / wrong-patch-target claim."""
    provider = _fake_with_verdict({0: True})

    reflect_findings([_HIGH], _CTX, _CFG, provider)

    system = provider.calls[0]["messages"][0]["content"].lower()
    assert "semantics" in system
    assert "import" in system and "await" in system
    assert "patch target" in system or "mock" in system


# ---------------------------------------------------------------------------
# PIECE 3 — actionability tiering (broad vs safe self-contained)
# ---------------------------------------------------------------------------


def test_reflection_marks_finding_broad() -> None:
    """A {"index":0,"keep":true,"broad":true} verdict sets broad=True on the
    surviving finding."""
    text = json.dumps({"verdicts": [{"index": 0, "keep": True, "broad": True}]})
    provider = _fake_with_text(text)

    survivors = reflect_findings([_HIGH], _CTX, _CFG, provider)

    assert len(survivors) == 1
    assert survivors[0].broad is True


def test_reflection_defaults_broad_false_when_absent() -> None:
    """A verdict that omits broad keeps the finding non-broad."""
    provider = _fake_with_text(_envelope([(0, True)]))

    survivors = reflect_findings([_HIGH], _CTX, _CFG, provider)

    assert survivors[0].broad is False


def test_reflect_prompt_asks_for_broad_flag() -> None:
    """The system prompt asks the auditor for a per-verdict broad flag."""
    provider = _fake_with_verdict({0: True})

    reflect_findings([_HIGH], _CTX, _CFG, provider)

    system = provider.calls[0]["messages"][0]["content"].lower()
    assert "broad" in system


def test_reflection_uses_reflect_model_when_set() -> None:
    """A configured reflect_model is the model passed to the reflection call,
    overriding the review model."""
    cfg = ReviewConfig(
        provider=Provider.ollama, model="llama3", reflect_model="bigger-judge"
    )
    provider = _fake_with_verdict({0: True})

    reflect_findings([_HIGH], _CTX, cfg, provider)

    assert provider.calls[0]["model"] == "bigger-judge"


def test_reflection_falls_back_to_model_without_reflect_model() -> None:
    """With no reflect_model, the reflection call uses cfg.model."""
    provider = _fake_with_verdict({0: True})  # _CFG has reflect_model=None

    reflect_findings([_HIGH], _CTX, _CFG, provider)

    assert provider.calls[0]["model"] == "llama3"


def test_grounding_truncates_huge_file_within_budget() -> None:
    """A file_contents far larger than the token budget is head+tail-truncated so
    the user message stays within roughly the budget."""
    from lgtmaybe.engine.compress import count_tokens

    huge = "\n".join(f"line {i} of a very large file here" for i in range(20_000)) + "\n"
    ctx = _CTX.model_copy(update={"file_contents": {"a.py": huge}})
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", max_input_tokens=4_000)
    provider = _fake_with_verdict({0: True})

    reflect_findings([_HIGH], ctx, cfg, provider)

    user = provider.calls[0]["messages"][1]["content"]
    # The whole file (~120k tokens) would blow the 4k budget; truncation keeps it bounded.
    assert count_tokens(user) <= cfg.max_input_tokens * 2
