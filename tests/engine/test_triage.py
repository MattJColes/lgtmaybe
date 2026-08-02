"""Two-stage triage routing (P3).

An optional cheap ``triage_model`` runs first over the compressed per-file
diffs, filtering files that plainly need no review (pure formatting, trivial
renames, generated content that slipped the skip filter) and ranking the rest
by risk — the strong ``model`` then reviews only what survives. Contracts:

- **security floor**: files on security-relevant paths, patches carrying
  security-relevant tokens, files with static-analysis hits, and large hunks
  ALWAYS reach the strong model — triage can never skip them;
- triage off (``triage_model`` unset, the default) reproduces current
  single-model behaviour exactly;
- an unparseable/failed triage call reviews everything (safe default);
- a file the verdict doesn't mention is reviewed (safe default);
- everything routes through the one ProviderClient port, so an all-ollama
  config pays nothing.
"""

from __future__ import annotations

import json

from lgtmaybe.core.models import (
    PRContext,
    Provider,
    ProviderResult,
    ReviewConfig,
    Severity,
)
from lgtmaybe.engine import LLMReviewEngine
from lgtmaybe.engine.static_analysis import ToolFinding
from lgtmaybe.engine.triage import always_escalate, triage_files
from tests.fakes import FakeProvider

BORING = ("docs/readme.md", "@@ -1 +1,2 @@\n old\n+typo fix\n")
AUTH = ("src/auth/login.py", "@@ -1 +1,2 @@\n old\n+check_password(user)\n")
PLAIN = ("src/util.py", "@@ -1 +1,2 @@\n old\n+return x + 1\n")


def _cfg(**overrides: object) -> ReviewConfig:
    return ReviewConfig(
        provider=Provider.ollama,
        model="llama3",
        **overrides,  # type: ignore[arg-type]
    )


def _verdict_provider(verdicts: list[dict[str, object]]) -> FakeProvider:
    text = json.dumps({"files": verdicts})
    return FakeProvider(result=ProviderResult(text=text, input_tokens=5, output_tokens=5))


# ---------------------------------------------------------------------------
# the deterministic security floor
# ---------------------------------------------------------------------------


def test_security_relevant_paths_always_escalate() -> None:
    for path in (
        "src/auth/login.py",
        "app/crypto/keys.py",
        "db/migrations/0042_add_users.sql",
        ".github/workflows/ci.yml",
        "infra/main.tf",
        "Dockerfile",
        "pyproject.toml",
    ):
        assert always_escalate(path, "@@ -1 +1 @@\n+x = 1\n", hinted_paths=set()), path


def test_security_tokens_in_the_patch_escalate() -> None:
    patch = "@@ -1 +1,2 @@\n old\n+requests.get(url, verify=False)\n"
    assert always_escalate("src/plain.py", patch, hinted_paths=set())
    assert always_escalate("src/plain.py", "@@ -1 +1 @@\n+password = input()\n", hinted_paths=set())


def test_static_analysis_hit_escalates() -> None:
    assert always_escalate("src/plain.py", "@@ -1 +1 @@\n+x = 1\n", hinted_paths={"src/plain.py"})


def test_large_hunks_escalate() -> None:
    big = "@@ -1,250 +1,250 @@\n" + "\n".join(f"+line {i}" for i in range(250))
    assert always_escalate("docs/notes.md", big, hinted_paths=set())


def test_diff_file_headers_do_not_count_as_changed_lines() -> None:
    """Every ``split_by_file`` patch carries a ``---``/``+++`` pair; counting it
    made the documented 200-line floor really 198, escalating a file sitting
    just under the threshold."""
    body = "\n".join(f"+line {i}" for i in range(199))
    patch = (
        "diff --git a/docs/notes.md b/docs/notes.md\n"
        "--- a/docs/notes.md\n"
        "+++ b/docs/notes.md\n"
        f"@@ -1 +1,199 @@\n{body}\n"
    )
    assert not always_escalate("docs/notes.md", patch, hinted_paths=set())


def test_plain_small_file_does_not_escalate() -> None:
    assert not always_escalate("src/util.py", "@@ -1 +1 @@\n+return x + 1\n", hinted_paths=set())


def test_security_path_tokens_do_not_fire_inside_ordinary_words() -> None:
    """Short tokens (acl, sso, iam, token, session) are word-bounded so they
    don't substring-match ordinary words — an `oracle_db.py` escalating via the
    `acl` token silently defeats triage's whole point."""
    for path in ("src/oracle_db.py", "src/professor.py", "src/tokenizer.py"):
        assert not always_escalate(path, "@@ -1 +1 @@\n+x = 1\n", hinted_paths=set()), path


def test_short_security_path_tokens_still_escalate_as_words() -> None:
    """The same short tokens still fire when they appear as real path words —
    `_`, `/`, `.` and `-` all count as separators (err toward escalation)."""
    for path in (
        "auth/session.py",
        "iam/policy.py",
        "sso/config.py",
        "app/access_token.py",
        "src/acl-rules.py",
    ):
        assert always_escalate(path, "@@ -1 +1 @@\n+x = 1\n", hinted_paths=set()), path


# ---------------------------------------------------------------------------
# triage_files
# ---------------------------------------------------------------------------


def test_triage_skips_only_what_the_model_marks_boring() -> None:
    provider = _verdict_provider(
        [
            {"path": BORING[0], "review": False, "risk": 0},
            {"path": PLAIN[0], "review": True, "risk": 6},
        ]
    )

    kept, skipped = triage_files([BORING, PLAIN], [], _cfg(triage_model="tiny"), provider)

    assert [p for p, _ in kept] == [PLAIN[0]]
    assert skipped == [BORING[0]]


def test_floor_files_survive_even_when_triage_says_skip() -> None:
    provider = _verdict_provider(
        [
            {"path": AUTH[0], "review": False, "risk": 0},  # hostile/cheap model lies
            {"path": BORING[0], "review": False, "risk": 0},
        ]
    )

    kept, skipped = triage_files([AUTH, BORING], [], _cfg(triage_model="tiny"), provider)

    assert [p for p, _ in kept] == [AUTH[0]]
    assert skipped == [BORING[0]]
    # The floor file never even reaches the triage prompt — nothing to argue with.
    sent = provider.calls[0]["messages"][1]["content"]
    assert AUTH[0] not in sent


def test_survivors_are_ranked_most_risky_first() -> None:
    low = ("src/a.py", "@@ -1 +1 @@\n+return 1\n")
    high = ("src/b.py", "@@ -1 +1 @@\n+return 2\n")
    provider = _verdict_provider(
        [
            {"path": low[0], "review": True, "risk": 2},
            {"path": high[0], "review": True, "risk": 9},
        ]
    )

    kept, _skipped = triage_files([low, high], [], _cfg(triage_model="tiny"), provider)

    assert [p for p, _ in kept] == [high[0], low[0]]


def test_unmentioned_file_is_reviewed() -> None:
    provider = _verdict_provider([])  # verdict names nothing

    kept, skipped = triage_files([PLAIN], [], _cfg(triage_model="tiny"), provider)

    assert [p for p, _ in kept] == [PLAIN[0]]
    assert skipped == []


def test_unparseable_triage_reviews_everything() -> None:
    provider = FakeProvider(
        result=ProviderResult(text="no json here", input_tokens=1, output_tokens=1)
    )

    kept, skipped = triage_files([BORING, PLAIN], [], _cfg(triage_model="tiny"), provider)

    assert [p for p, _ in kept] == [BORING[0], PLAIN[0]]
    assert skipped == []


def test_out_of_range_risk_reviews_everything() -> None:
    """The verdict schema declares risk as 0-10, so a value outside it is a
    verdict lgtmaybe cannot trust — and an untrusted verdict means review, never
    a guessed-down score that could skip a file."""
    provider = _verdict_provider([{"path": BORING[0], "review": False, "risk": 99}])

    kept, skipped = triage_files([BORING, PLAIN], [], _cfg(triage_model="tiny"), provider)

    assert [p for p, _ in kept] == [BORING[0], PLAIN[0]]
    assert skipped == []


def test_provider_failure_reviews_everything() -> None:
    class _Boom(FakeProvider):
        def complete(self, messages, model, **opts):  # type: ignore[no-untyped-def]
            raise RuntimeError("quota")

    kept, skipped = triage_files([BORING, PLAIN], [], _cfg(triage_model="tiny"), _Boom())

    assert [p for p, _ in kept] == [BORING[0], PLAIN[0]]
    assert skipped == []


def test_hints_feed_the_floor() -> None:
    hint = ToolFinding(
        tool="bandit", path=PLAIN[0], line=1, rule="B1", message="m", severity=Severity.medium
    )
    provider = _verdict_provider([{"path": PLAIN[0], "review": False, "risk": 0}])

    kept, skipped = triage_files([PLAIN], [hint], _cfg(triage_model="tiny"), provider)

    assert [p for p, _ in kept] == [PLAIN[0]]  # hinted file can't be skipped
    assert skipped == []


def test_all_floor_files_makes_no_triage_call() -> None:
    provider = _verdict_provider([])

    kept, _ = triage_files([AUTH], [], _cfg(triage_model="tiny"), provider)

    assert [p for p, _ in kept] == [AUTH[0]]
    assert provider.calls == []  # nothing skippable → don't pay for the call


def test_triage_call_uses_the_triage_model_via_the_same_provider() -> None:
    provider = _verdict_provider([{"path": PLAIN[0], "review": True, "risk": 5}])

    triage_files([PLAIN], [], _cfg(triage_model="tiny-model"), provider)

    assert provider.calls[0]["model"] == "tiny-model"


# ---------------------------------------------------------------------------
# engine integration
# ---------------------------------------------------------------------------

_TWO_FILE_CTX = PRContext(
    diff=(
        "diff --git a/docs/readme.md b/docs/readme.md\n@@ -1 +1,2 @@\n old\n+typo fix\n"
        "diff --git a/src/util.py b/src/util.py\n@@ -1 +1,2 @@\n old\n+return x + 1\n"
    ),
    changed_files=["docs/readme.md", "src/util.py"],
    base_sha="abc",
    head_sha="def",
    repo="org/repo",
    pr_number=4,
)


class _RoutingProvider(FakeProvider):
    """Serve the triage verdict on the triage call, findings elsewhere."""

    def complete(self, messages, model, **opts):  # type: ignore[no-untyped-def]
        self.calls.append({"messages": messages, "model": model, **opts})
        if model == "tiny":
            text = json.dumps({"files": [{"path": "docs/readme.md", "review": False, "risk": 0}]})
        else:
            text = json.dumps({"findings": []})
        return ProviderResult(text=text, input_tokens=1, output_tokens=1)


def test_engine_reviews_only_triage_survivors() -> None:
    provider = _RoutingProvider()
    engine = LLMReviewEngine(provider)
    cfg = _cfg(triage_model="tiny", reflect=False)

    _findings, summary = engine.review(_TWO_FILE_CTX, cfg)

    lens_calls = [c for c in provider.calls if c["model"] != "tiny"]
    assert lens_calls, "the strong model still reviews the survivors"
    for call in lens_calls:
        sent = call["messages"][1]["content"]
        assert "src/util.py" in sent
        assert "docs/readme.md" not in sent
    # The summary is transparent about what triage skipped.
    assert "1" in summary and "triage" in summary.lower()


def test_engine_without_triage_model_never_makes_a_triage_call() -> None:
    provider = _RoutingProvider()
    engine = LLMReviewEngine(provider)
    cfg = _cfg(reflect=False)  # triage_model defaults to None

    engine.review(_TWO_FILE_CTX, cfg)

    assert all(c["model"] != "tiny" for c in provider.calls)
    sent = provider.calls[0]["messages"][1]["content"]
    assert "docs/readme.md" in sent  # nothing skipped
