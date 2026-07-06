"""Structural checks on the eval fixtures — they load and parse as expected.

These are pure (no model): they guard that a fixture's diff is well-formed and
that the large multi-file fixture really exercises the multi-file path, so a
broken fixture fails fast in the pytest gate rather than only in the live
ollama e2e run.
"""

from __future__ import annotations

import json

import pytest

from evals import run as run_mod
from lgtmaybe.core.diffparse import changed_line_index, split_by_file
from lgtmaybe.core.models import PRContext, Provider, ProviderResult, ReviewConfig, ReviewFinding
from lgtmaybe.core.ports import ProviderClient
from lgtmaybe.engine.astgrep import ast_grep_available, build_symbol_resolver
from lgtmaybe.engine.compress import split_patch_into_hunks
from lgtmaybe.engine.reflect import reflect_findings
from lgtmaybe.github import is_reviewable
from lgtmaybe.local import local_file_reader

# The four live false-positive fixtures Track C adds: each plants a genuine catch
# plus forbidden traps drawn from real over-eager reviewer claims.
_FP_FIXTURES = ["lazy-imports", "split-hunks", "cloud-semantics", "test-harness"]

_VIBE_FILES = {
    "src/api/handlers.py",
    "src/db/queries.py",
    "src/utils/shell.py",
    "src/auth/session.py",
    "config/settings.py",
    "src/api/pagination.py",
}


def _fixture(name: str):
    for diff, manifest in run_mod._load_fixtures():
        if manifest.name == name:
            return diff, manifest
    raise AssertionError(f"fixture {name!r} not found")


def test_all_fixtures_load() -> None:
    """Every fixture dir parses into a (diff, manifest) pair with expected findings."""
    fixtures = run_mod._load_fixtures()
    assert fixtures, "no fixtures discovered"
    for diff, manifest in fixtures:
        assert diff.strip()
        assert manifest.expected, f"{manifest.name} has no expected findings"


def test_vibe_multifile_spans_all_reviewable_files() -> None:
    """The large fixture splits into all six files and none is filtered as generated."""
    diff, manifest = _fixture("vibe-multifile")

    paths = {path for path, _ in split_by_file(diff, [manifest.changed_file])}
    assert paths == _VIBE_FILES

    # All of them must survive the reviewable filter (no lockfiles/vendored noise).
    assert all(is_reviewable(p) for p in paths)


def test_vibe_multifile_has_high_signal_and_subtle_findings() -> None:
    """The manifest mixes easy security catches with subtler correctness bugs."""
    _diff, manifest = _fixture("vibe-multifile")
    labels = " ".join(e.label.lower() for e in manifest.expected)
    # A 0.6B CI model should be able to clear the 0.2 recall bar on these.
    assert "sql injection" in labels
    assert "shell=true" in labels
    assert "eval()" in labels
    # ...and the subtler bugs that prove depth.
    assert "off-by-one" in labels


@pytest.mark.parametrize("name", ["rlm-bigfile", "rlm-pipeline"])
def test_rlm_fixture_is_one_multi_hunk_file(name: str) -> None:
    """Each RLM benchmark fixture must be a single file with several hunks — that's
    the shape that exercises the recursive walk (an over-budget file split into
    per-hunk calls). Guards against an edit that flattens one to a single hunk and
    silently makes the benchmark a no-op."""
    from lgtmaybe.engine.compress import split_patch_into_hunks

    diff, manifest = _fixture(name)
    parts = split_by_file(diff, [manifest.changed_file])
    assert len(parts) == 1, f"{name} must be a single file"
    _path, patch = parts[0]
    assert len(split_patch_into_hunks(patch)) >= 3, f"{name} needs several hunks"


def test_cross_file_fp_fixture_has_expected_and_forbidden() -> None:
    """The cross-file fixture loads with a genuine in-diff catch plus forbidden traps —
    the diff alone (no sibling file_contents) so the guard is genuinely unseen, which is
    the real-world shape that produced the invalid findings."""
    diff, manifest = _fixture("cross-file-fp")
    assert diff.strip()
    assert manifest.expected, "needs a real in-diff finding so recall stays meaningful"
    assert manifest.forbidden, "needs forbidden (cross-file false-positive) traps"


def _changed_lines(diff: str, path: str, side: str = "RIGHT") -> set[int]:
    """The set of new-file (RIGHT) line numbers that the diff actually changes."""
    index = changed_line_index(diff)
    return {line for line, _text in index.get((path, side), [])}


@pytest.mark.parametrize("name", _FP_FIXTURES)
def test_fp_fixture_loads_with_expected_and_forbidden(name: str) -> None:
    """Each live FP fixture loads with a genuine catch AND forbidden traps, every
    entry has keywords, and every expected/forbidden line is a real changed line —
    mirrors the cross-file-fp coverage so a malformed fixture fails in the gate."""
    diff, manifest = _fixture(name)
    assert diff.strip()
    assert manifest.expected, f"{name}: needs a real in-diff catch so recall stays meaningful"
    assert manifest.forbidden, f"{name}: needs forbidden (false-positive) traps"
    assert all(e.keywords for e in manifest.expected), f"{name}: an expected entry has no keywords"
    assert all(f.keywords for f in manifest.forbidden), f"{name}: a forbidden entry has no keywords"

    changed = _changed_lines(diff, manifest.changed_file)
    assert changed, f"{name}: diff has no changed RIGHT lines"
    for entry in manifest.expected + manifest.forbidden:
        assert entry.line in changed, (
            f"{name}: line {entry.line} ({entry.label!r}) is not a changed line; "
            f"changed lines are {sorted(changed)}"
        )


def test_split_hunks_fixture_is_multi_hunk() -> None:
    """split-hunks must be a single file split into >=2 hunks that both touch the
    same def (signature in one hunk, body edit in another) — the exact shape that
    tempts a model into a bogus "duplicate definition" finding."""
    diff, manifest = _fixture("split-hunks")
    parts = split_by_file(diff, [manifest.changed_file])
    assert len(parts) == 1, "split-hunks must be a single file"
    _path, patch = parts[0]
    hunks = split_patch_into_hunks(patch)
    assert len(hunks) >= 2, "split-hunks needs at least two hunks"
    touching = [h for h in hunks if "process_batch" in h]
    assert len(touching) >= 2, "both hunks must touch the same def (process_batch)"


def test_cross_file_fp_ships_corpus_refuting_its_forbidden_traps() -> None:
    """The cross-file-fp fixture now carries an on-disk corpus of the *unshown*
    files its forbidden traps hinge on, so symbol resolution has something real to
    find. The defining symbols are present in that corpus."""
    _diff, manifest = _fixture("cross-file-fp")

    assert manifest.corpus_root is not None, "cross-file-fp must ship a repo/ corpus"
    ledger = manifest.corpus_root / "migrations" / "ledger.py"
    models = manifest.corpus_root / "migrations" / "models.py"
    assert ledger.is_file() and models.is_file()
    ledger_text = ledger.read_text()
    # The idempotency guard + tenant filter the "no guard"/"tenant_id None" traps deny.
    assert "def already_applied" in ledger_text
    assert "def mark_applied" in ledger_text
    assert "def pending" in ledger_text
    # The V2 shape the "field absent from V2" trap denies.
    assert "class SavedSubmittalSetV2" in models.read_text()


def test_loader_sets_corpus_root_only_for_fixtures_with_a_repo_dir() -> None:
    """The loader attaches corpus_root for fixtures that ship a repo/ dir and leaves
    it None for the rest — so wiring a resolver never touches a corpus-less fixture."""
    by_name = {m.name: m for _diff, m in run_mod._load_fixtures()}
    assert by_name["cross-file-fp"].corpus_root is not None
    # A plain single-file fixture has no corpus.
    assert by_name["badcode"].corpus_root is None


@pytest.mark.skipif(not ast_grep_available(), reason="ast-grep binary not installed")
def test_real_ast_grep_resolves_cross_file_symbols_in_the_corpus() -> None:
    """Real ast-grep, rooted at the fixture corpus, maps each deferred symbol to the
    file that defines it — the resolution the auditor relies on."""
    _diff, manifest = _fixture("cross-file-fp")
    root = manifest.corpus_root
    resolve = build_symbol_resolver(lambda: root)
    assert resolve is not None
    assert resolve("already_applied") == ["migrations/ledger.py"]
    assert resolve("SavedSubmittalSetV2") == ["migrations/models.py"]


class _ScriptedProvider(ProviderClient):
    """Returns a different canned text per successive call (records each call)."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = texts
        self.calls: list[dict[str, object]] = []

    def complete(self, messages, model, **opts):  # type: ignore[no-untyped-def]
        idx = min(len(self.calls), len(self._texts) - 1)
        self.calls.append({"messages": messages})
        return ProviderResult(text=self._texts[idx], input_tokens=5, output_tokens=5)


@pytest.mark.skipif(not ast_grep_available(), reason="ast-grep binary not installed")
def test_end_to_end_symbol_deferral_drops_forbidden_finding_with_real_ast_grep() -> None:
    """The whole chain, no fakes in the resolution path: a finding is deferred on a
    SYMBOL; real ast-grep finds its file in the corpus; the real read-only reader
    loads it; the definition reaches the recheck prompt; the cross-file false
    positive is dropped. This is the proof the feature works integrated, not just
    unit by unit."""
    _diff, manifest = _fixture("cross-file-fp")
    root = manifest.corpus_root
    assert root is not None

    finding = ReviewFinding(
        path="migrations/0003_backfill.py",
        line=12,
        severity="medium",
        title="backfill has no idempotency guard",
        body="Re-running backfill could copy rows twice.",
    )
    # Auditor: 1st call defers on the symbol `pending`; 2nd call (with the file in
    # context) drops the finding — the ledger shows pending() is idempotent.
    provider = _ScriptedProvider(
        [
            json.dumps(
                {"verdicts": [{"index": 0, "keep": False, "broad": False, "needs": ["pending"]}]}
            ),
            json.dumps({"verdicts": [{"index": 0, "keep": False, "broad": False, "needs": []}]}),
        ]
    )
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")
    ctx = PRContext(
        diff="--- a/migrations/0003_backfill.py\n+++ b/migrations/0003_backfill.py\n",
        changed_files=["migrations/0003_backfill.py"],
        base_sha="0",
        head_sha="1",
        repo="eval/eval",
        pr_number=0,
    )

    survivors = reflect_findings(
        [finding],
        ctx,
        cfg,
        provider,
        fetch_file=local_file_reader(root),
        resolve_symbol=build_symbol_resolver(lambda: root),
    )

    # The real ledger.py reached the recheck prompt via real ast-grep + real reader.
    recheck_prompt = " ".join(
        m["content"] for m in provider.calls[1]["messages"] if isinstance(m, dict)
    )
    assert "def pending" in recheck_prompt
    assert survivors == []  # cross-file false positive correctly dropped


def test_fixtures_cover_performance_and_complexity_lenses() -> None:
    """Both fixtures plant a performance and a complexity issue so the e2e exercises
    all seven code lenses, not just security + correctness. (The intent lens needs a
    stated intent the fixtures don't carry, so the engine skips it there.) Guards
    against a future edit silently dropping these lower-severity lenses from the
    live recall check."""
    for name in ("badcode", "vibe-multifile"):
        _diff, manifest = _fixture(name)
        keywords = " ".join(k.lower() for e in manifest.expected for k in e.keywords)
        assert "n+1" in keywords or "quadratic" in keywords, f"{name}: no performance finding"
        assert "complexity" in keywords and "cyclomatic" in keywords, (
            f"{name}: no complexity finding"
        )


# ---------------------------------------------------------------------------
# static-hints fixture (F1 A/B) + head/ loading
# ---------------------------------------------------------------------------


def test_static_hints_head_matches_the_diff() -> None:
    """The head/ text must be exactly what the diff adds — a drifted copy would
    lint different code than the model reviews."""
    diff, manifest = _fixture("static-hints")

    assert manifest.head_root is not None
    head = (manifest.head_root / "report.py").read_text()
    added = [
        line[1:] for line in diff.splitlines() if line.startswith("+") and line != "+++ b/report.py"
    ]
    assert added == head.splitlines()


def test_static_hints_plants_tool_detectable_bugs() -> None:
    """Each planted bug is a pattern bandit fires on deterministically, so an
    A/B run (--static-analysis on/off) measures the fusion's recall delta."""
    _diff, manifest = _fixture("static-hints")
    head = (manifest.head_root / "report.py").read_text()  # type: ignore[union-attr]

    for marker in ("yaml.load", "verify=False", "shell=True", "hashlib.md5"):
        assert marker in head, marker
    # Every expected finding sits on a real added line of the diff.
    lines = head.splitlines()
    for exp in manifest.expected:
        assert 1 <= exp.line <= len(lines)


def test_head_root_set_only_for_fixtures_with_a_head_dir() -> None:
    fixtures = run_mod._load_fixtures()
    by_name = {m.name: m for _d, m in fixtures}
    assert by_name["static-hints"].head_root is not None
    assert by_name["badcode"].head_root is None


def test_eval_ctx_loads_head_files_as_file_contents() -> None:
    from evals.scorer import _eval_ctx

    diff, manifest = _fixture("static-hints")
    ctx = _eval_ctx(diff, manifest)

    assert "report.py" in ctx.file_contents
    assert "yaml.load" in ctx.file_contents["report.py"]


def test_eval_ctx_without_head_dir_has_no_file_contents() -> None:
    from evals.scorer import _eval_ctx

    diff, manifest = _fixture("badcode")
    assert _eval_ctx(diff, manifest).file_contents == {}
