"""Guards in the engine: file cap and generated-file skipping."""

from __future__ import annotations

import json

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewConfig,
    ReviewFinding,
    Severity,
)
from lgtmaybe.core.ports import Message
from lgtmaybe.engine import LLMReviewEngine
from tests.fakes import FakeProvider


def _diff_for(paths: list[str]) -> str:
    """Build a minimal multi-file unified diff adding one line to each path."""
    chunks = []
    for p in paths:
        chunks.append(
            f"diff --git a/{p} b/{p}\n"
            f"index 0000001..0000002 100644\n"
            f"--- a/{p}\n"
            f"+++ b/{p}\n"
            f"@@ -1,1 +1,2 @@\n"
            f" first\n"
            f"+added line in {p}\n"
        )
    return "".join(chunks)


def _ctx(paths: list[str]) -> PRContext:
    return PRContext(
        diff=_diff_for(paths),
        changed_files=paths,
        base_sha="abc",
        head_sha="def",
        repo="org/repo",
        pr_number=1,
    )


class _Provider(FakeProvider):
    """Returns the given findings (as JSON) for review calls, keep-all for reflection.

    Robust to per-category fan-out: every review call (whichever category) returns
    the findings; only the reflection call returns the keep-all verdict, told apart
    by its distinctive system prompt.
    """

    def __init__(self, findings: list[ReviewFinding]) -> None:
        super().__init__()
        self._payload = json.dumps([f.model_dump(mode="json") for f in findings])
        self._verdict = json.dumps({i: True for i in range(len(findings))})

    def complete(self, messages: list[Message], model: str, **opts: object) -> ProviderResult:
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        system = messages[0]["content"]
        if "auditing another reviewer" in system:  # the reflection pass
            return ProviderResult(text=self._verdict, input_tokens=5, output_tokens=5)
        return ProviderResult(text=self._payload, input_tokens=10, output_tokens=20)


_A_FINDING = ReviewFinding(
    path="a.py",
    line=1,
    severity=Severity.high,
    title="bug",
    body="broken",
    failure_scenario="When the changed line runs, the operation fails instead of completing.",
)


def test_clean_review_says_lgtm() -> None:
    """A review with no findings posts a 👍 LGTM! summary (still naming the model)."""
    provider = _Provider([])
    engine = LLMReviewEngine(provider)
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")

    findings, summary = engine.review(_ctx(["a.py"]), cfg)

    assert findings == []
    assert "LGTM" in summary
    assert "llama3" in summary  # the model line is still present


def test_review_with_findings_does_not_say_lgtm() -> None:
    provider = _Provider([_A_FINDING])
    engine = LLMReviewEngine(provider)
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")

    _findings, summary = engine.review(_ctx(["a.py"]), cfg)

    assert "LGTM" not in summary


def test_file_cap_reviews_top_n_with_notice() -> None:
    provider = _Provider([_A_FINDING])
    engine = LLMReviewEngine(provider)
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", max_files=2)

    findings, summary = engine.review(_ctx(["f1.py", "f2.py", "f3.py"]), cfg)

    assert "2" in summary and "3" in summary
    assert "file" in summary.lower()
    # Only the first two files' patches reached the provider.
    sent = " ".join(msg.get("content", "") for call in provider.calls for msg in call["messages"])
    assert "added line in f1.py" in sent
    assert "added line in f2.py" in sent
    assert "added line in f3.py" not in sent


def _big_diff(path: str, added_lines: int) -> str:
    """A single-file diff adding *added_lines* lines to *path*."""
    body = "".join(f"+line {i} in {path}\n" for i in range(added_lines))
    return (
        f"diff --git a/{path} b/{path}\n"
        f"index 0000001..0000002 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -1,1 +1,{added_lines + 1} @@\n"
        f" first\n" + body
    )


def _ctx_from(diff: str, paths: list[str]) -> PRContext:
    return PRContext(
        diff=diff,
        changed_files=paths,
        base_sha="abc",
        head_sha="def",
        repo="org/repo",
        pr_number=1,
    )


def test_oversized_file_is_skipped_with_a_notice() -> None:
    """A hand-named data blob no pattern can catch is skipped on size alone.

    The motivating case is a generated 154k-line `clause_index.json`: nothing in
    `is_reviewable` can know that name is machine-written, so the line cap is the
    only deterministic defence — and, like the file cap, the skip must be named in
    the summary rather than read as "everything was covered".
    """
    provider = _Provider([_A_FINDING])
    engine = LLMReviewEngine(provider)
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", max_file_diff_lines=10)

    paths = ["data/clause_index.json", "src/app.py"]
    diff = _big_diff("data/clause_index.json", 50) + _diff_for(["src/app.py"])
    _findings, summary = engine.review(_ctx_from(diff, paths), cfg)

    sent = " ".join(msg.get("content", "") for call in provider.calls for msg in call["messages"])
    assert "line 0 in data/clause_index.json" not in sent
    assert "added line in src/app.py" in sent
    assert "clause_index.json" in summary
    assert "max_file_diff_lines" in summary


def test_oversized_skip_does_not_consume_the_file_cap() -> None:
    """A size-skipped file is out of the review entirely, exactly like a lockfile
    — so it must not count towards `max_files` and trigger a spurious cap notice."""
    provider = _Provider([])
    engine = LLMReviewEngine(provider)
    cfg = ReviewConfig(
        provider=Provider.ollama, model="llama3", max_files=2, max_file_diff_lines=10
    )

    diff = _big_diff("blob.json", 50) + _diff_for(["a.py", "b.py"])
    _findings, summary = engine.review(_ctx_from(diff, ["blob.json", "a.py", "b.py"]), cfg)

    assert "file cap" not in summary
    sent = " ".join(msg.get("content", "") for call in provider.calls for msg in call["messages"])
    assert "added line in a.py" in sent
    assert "added line in b.py" in sent


def test_zero_disables_the_file_size_cap() -> None:
    provider = _Provider([])
    engine = LLMReviewEngine(provider)
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3", max_file_diff_lines=0)

    _findings, summary = engine.review(_ctx_from(_big_diff("blob.json", 50), ["blob.json"]), cfg)

    sent = " ".join(msg.get("content", "") for call in provider.calls for msg in call["messages"])
    assert "line 0 in blob.json" in sent
    assert "max_file_diff_lines" not in summary


def test_generated_and_lockfiles_are_skipped() -> None:
    provider = _Provider([_A_FINDING])
    engine = LLMReviewEngine(provider)
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")

    engine.review(_ctx(["package-lock.json", "src/app.py"]), cfg)

    sent = " ".join(msg.get("content", "") for call in provider.calls for msg in call["messages"])
    assert "added line in src/app.py" in sent
    assert "added line in package-lock.json" not in sent
