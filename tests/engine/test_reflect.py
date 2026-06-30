"""Tests for reflect.py — self-reflection / false-positive filter."""

from __future__ import annotations

import json
import logging

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReflectionResult,
    ReviewConfig,
    ReviewFinding,
    Severity,
)
from lgtmaybe.core.ports import ProviderClient
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


class _RaisingProvider(ProviderClient):
    """A ProviderClient whose complete() always raises (quota/auth/network)."""

    def complete(self, messages: list[dict[str, str]], model: str, **opts: object):
        raise RuntimeError("provider exploded")


class _ListHandler(logging.Handler):
    """Collects emitted LogRecords for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_reflection_failure_keeps_all_and_logs() -> None:
    """When the audit call raises, keep every finding (safe default) AND warn.

    Swallowing the cause silently makes an always-failing reflection pass look
    identical to "nothing to prune" — the repo convention is errors must surface.
    The reflect logger does not propagate to root, so attach a handler directly.
    """
    from lgtmaybe.engine import reflect as reflect_mod

    handler = _ListHandler()
    reflect_mod._log.addHandler(handler)
    try:
        survivors = reflect_findings([_HIGH, _LOW_CONF], _CTX, _CFG, _RaisingProvider())
    finally:
        reflect_mod._log.removeHandler(handler)

    assert survivors == [_HIGH, _LOW_CONF]  # safe default — nothing dropped
    warnings = [r for r in handler.records if r.levelno >= logging.WARNING]
    assert warnings, "expected a warning when the reflection pass fails"
    assert any("reflection" in r.getMessage().lower() for r in warnings)
    # The cause must be attached so logs name why it failed, not just that it did.
    assert any(r.exc_info for r in warnings)


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
    ctx = _CTX.model_copy(update={"file_contents": {"a.py": f"key = '{secret}'\n"}})
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
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", reflect_model="bigger-judge")
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


# ---------------------------------------------------------------------------
# TRACK D — bounded retrieval escalation (verify, don't cull)
#
# When the auditor would drop a finding ONLY because it can't see a referenced
# file, it DEFERS (names what it needs); the engine fetches the file read-only
# and the auditor re-judges. Bounded (<= MAX_HOPS), fork-safe, redacted.
# ---------------------------------------------------------------------------


class _ScriptedProvider(ProviderClient):
    """A ProviderClient that returns a different canned text per successive call.

    Records every call's messages/model/opts (like FakeProvider) so a test can
    assert what the recheck prompt contained. Once the script is exhausted it
    keeps returning its last entry.
    """

    def __init__(self, texts: list[str]) -> None:
        self._texts = texts
        self.calls: list[dict[str, object]] = []

    def complete(self, messages: list[dict[str, str]], model: str, **opts: object):
        idx = min(len(self.calls), len(self._texts) - 1)
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        return ProviderResult(text=self._texts[idx], input_tokens=5, output_tokens=5)


def _needs_envelope(index: int, needs: list[str]) -> str:
    """A verdict that DEFERS finding *index* by naming the files it needs."""
    return json.dumps(
        {"verdicts": [{"index": index, "keep": False, "broad": False, "needs": needs}]}
    )


class _RecordingFetcher:
    """A read-only fetch_file double that records every path it was asked for."""

    def __init__(self, files: dict[str, str]) -> None:
        self._files = files
        self.calls: list[str] = []

    def __call__(self, path: str) -> str | None:
        self.calls.append(path)
        return self._files.get(path)


def test_defer_fetches_and_recheck_keeps_finding() -> None:
    """First verdict defers (needs other.py); after the fetch the recheck keeps it.
    The fetched (redacted) text reaches the recheck prompt, and the finding survives."""
    provider = _ScriptedProvider(
        [
            _needs_envelope(0, ["other.py"]),  # 1st call: defer
            _envelope([(0, True)]),  # 2nd call: confirm keep
        ]
    )
    fetcher = _RecordingFetcher({"other.py": "def referenced():\n    return 42\n"})

    survivors = reflect_findings([_HIGH], _CTX, _CFG, provider, fetch_file=fetcher)

    assert fetcher.calls == ["other.py"]
    # The recheck (2nd) call's user message carried the fetched text.
    recheck_user = provider.calls[1]["messages"][1]["content"]
    assert "def referenced():" in recheck_user
    assert _HIGH in survivors


def test_defer_then_confirm_drops_finding() -> None:
    """Same defer, but the recheck (with the file) confirms it's a false positive."""
    provider = _ScriptedProvider(
        [
            _needs_envelope(0, ["other.py"]),
            _envelope([(0, False)]),  # recheck: drop
        ]
    )
    fetcher = _RecordingFetcher({"other.py": "x = 1\n"})

    survivors = reflect_findings([_HIGH], _CTX, _CFG, provider, fetch_file=fetcher)

    assert fetcher.calls == ["other.py"]
    assert survivors == []


def test_defer_hop_cap_stops_and_drops() -> None:
    """An auditor that ALWAYS defers stops after MAX_HOPS and drops the finding —
    no infinite loop. The number of auditor calls is bounded."""
    from lgtmaybe.engine.retrieve import MAX_HOPS

    provider = _ScriptedProvider([_needs_envelope(0, ["other.py"])])  # always defers
    fetcher = _RecordingFetcher({"other.py": "y = 2\n"})

    survivors = reflect_findings([_HIGH], _CTX, _CFG, provider, fetch_file=fetcher)

    assert survivors == []  # unresolved deferral → dropped
    # 1 initial pass + at most MAX_HOPS recheck passes.
    assert len(provider.calls) <= 1 + MAX_HOPS


def test_defer_without_fetcher_drops_finding() -> None:
    """A deferred verdict with no fetcher wired → finding dropped, no crash."""
    provider = _ScriptedProvider([_needs_envelope(0, ["other.py"])])

    survivors = reflect_findings([_HIGH], _CTX, _CFG, provider, fetch_file=None)

    assert survivors == []


def test_resolver_only_calls_injected_fetcher() -> None:
    """Fork-safety: the only I/O the resolver does is the injected read-only
    fetch_file. A recording fetcher captures the single path; nothing else."""
    provider = _ScriptedProvider([_needs_envelope(0, ["other.py"]), _envelope([(0, True)])])
    fetcher = _RecordingFetcher({"other.py": "ok = True\n"})

    reflect_findings([_HIGH], _CTX, _CFG, provider, fetch_file=fetcher)

    assert fetcher.calls == ["other.py"]  # exactly one read-only fetch, no other path


def test_symbol_deferral_resolves_via_ast_grep_resolver() -> None:
    """The auditor defers by naming a SYMBOL (not a path). The symbol resolver
    (ast-grep) maps it to its defining file, which is then fetched read-only and
    reaches the recheck prompt — closing the gap a bare symbol used to dead-end in.
    """
    provider = _ScriptedProvider(
        [
            _needs_envelope(0, ["already_applied"]),  # defer on a symbol name
            _envelope([(0, False)]),  # recheck: the guard exists → false positive
        ]
    )
    fetcher = _RecordingFetcher({"pkg/ledger.py": "def already_applied(run_id):\n    ...\n"})

    def resolve_symbol(symbol: str) -> list[str]:
        return ["pkg/ledger.py"] if symbol == "already_applied" else []

    survivors = reflect_findings(
        [_HIGH], _CTX, _CFG, provider, fetch_file=fetcher, resolve_symbol=resolve_symbol
    )

    # The bare name is tried as a path first (miss), then resolved to its file.
    assert fetcher.calls == ["already_applied", "pkg/ledger.py"]
    recheck_user = provider.calls[1]["messages"][1]["content"]
    assert "def already_applied(run_id):" in recheck_user
    assert survivors == []  # recheck saw the guard and dropped the cross-file FP


def test_fetched_file_secret_never_reaches_recheck_prompt() -> None:
    """A secret in a fetched file is redacted before the recheck prompt is built."""
    secret = "AKIA" + "B" * 16
    provider = _ScriptedProvider([_needs_envelope(0, ["secrets.py"]), _envelope([(0, True)])])
    fetcher = _RecordingFetcher({"secrets.py": f"token = '{secret}'\n"})

    reflect_findings([_HIGH], _CTX, _CFG, provider, fetch_file=fetcher)

    recheck_user = provider.calls[1]["messages"][1]["content"]
    assert secret not in recheck_user
    assert "[REDACTED]" in recheck_user


def test_reflect_prompt_explains_needs_deferral() -> None:
    """The auditor system prompt tells the model to set `needs` instead of
    dropping a finding it can't verify for lack of a file."""
    provider = _fake_with_verdict({0: True})

    reflect_findings([_HIGH], _CTX, _CFG, provider)

    system = provider.calls[0]["messages"][0]["content"].lower()
    assert "needs" in system


def test_kept_and_deferred_findings_handled_together() -> None:
    """A mixed verdict: keep finding 0 outright, defer finding 1. After fetch the
    deferred one is confirmed kept — both survive."""
    provider = _ScriptedProvider(
        [
            json.dumps(
                {
                    "verdicts": [
                        {"index": 0, "keep": True, "broad": False, "needs": []},
                        {"index": 1, "keep": False, "broad": False, "needs": ["other.py"]},
                    ]
                }
            ),
            # recheck runs on the deferred subset only (one finding → index 0).
            _envelope([(0, True)]),
        ]
    )
    fetcher = _RecordingFetcher({"other.py": "helper = 1\n"})

    survivors = reflect_findings([_HIGH, _LOW_CONF], _CTX, _CFG, provider, fetch_file=fetcher)

    assert _HIGH in survivors
    assert _LOW_CONF in survivors
