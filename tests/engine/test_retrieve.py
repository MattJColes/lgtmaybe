"""Tests for retrieve.py — bounded read-only file fetching for deferred verdicts."""

from __future__ import annotations

from lgtmaybe.engine.compress import count_tokens
from lgtmaybe.engine.parse import coerce_needs, parse_needs
from lgtmaybe.engine.retrieve import (
    MAX_FETCH_FILES,
    MAX_HOPS,
    resolve_needs,
)


def test_resolve_needs_fetches_and_redacts() -> None:
    fetched_paths: list[str] = []

    def fetch(path: str) -> str | None:
        fetched_paths.append(path)
        return "def helper():\n    return 1\n"

    out = resolve_needs(["other.py"], fetch, already=set(), budget_tokens=10_000, max_files=5)

    assert fetched_paths == ["other.py"]
    assert out == {"other.py": "def helper():\n    return 1\n"}


def test_resolve_needs_redacts_secret() -> None:
    secret = "AKIA" + "A" * 16

    def fetch(path: str) -> str | None:
        return f"key = '{secret}'\n"

    out = resolve_needs(["s.py"], fetch, already=set(), budget_tokens=10_000, max_files=5)

    assert secret not in out["s.py"]
    assert "[REDACTED]" in out["s.py"]


def test_resolve_needs_skips_already_seen() -> None:
    calls: list[str] = []

    def fetch(path: str) -> str | None:
        calls.append(path)
        return "x = 1\n"

    out = resolve_needs(
        ["a.py", "b.py"],
        fetch,
        already={"a.py"},
        budget_tokens=10_000,
        max_files=5,
    )

    assert calls == ["b.py"]  # a.py already grounded — never fetched
    assert set(out) == {"b.py"}


def test_resolve_needs_skips_none_and_empty() -> None:
    def fetch(path: str) -> str | None:
        if path == "missing.py":
            return None
        if path == "empty.py":
            return ""
        return "real = 1\n"

    out = resolve_needs(
        ["missing.py", "empty.py", "real.py"],
        fetch,
        already=set(),
        budget_tokens=10_000,
        max_files=5,
    )

    assert set(out) == {"real.py"}


def test_resolve_needs_honours_max_files_cap() -> None:
    def fetch(path: str) -> str | None:
        return "x = 1\n"

    out = resolve_needs(
        ["a.py", "b.py", "c.py"],
        fetch,
        already=set(),
        budget_tokens=10_000,
        max_files=2,
    )

    assert len(out) == 2


def test_resolve_needs_honours_token_budget() -> None:
    huge = "\n".join(f"line {i} of a big file" for i in range(50_000)) + "\n"

    def fetch(path: str) -> str | None:
        if path == "huge.py":
            return huge
        return "small = 1\n"

    # Budget too small for the huge file, but the small one fits.
    out = resolve_needs(
        ["huge.py", "small.py"],
        fetch,
        already=set(),
        budget_tokens=500,
        max_files=5,
    )

    assert "huge.py" not in out  # skipped — would blow the budget
    assert "small.py" in out
    assert count_tokens("".join(out.values())) <= 500


def test_resolve_needs_maps_symbol_to_defining_file() -> None:
    # The auditor named a SYMBOL, not a path. Without a resolver it dead-ends;
    # with one (ast-grep), the symbol is mapped to its file and that file fetched.
    def fetch(path: str) -> str | None:
        return "def already_applied(): ...\n" if path == "pkg/ledger.py" else None

    def resolve_symbol(symbol: str) -> list[str]:
        return ["pkg/ledger.py"] if symbol == "already_applied" else []

    out = resolve_needs(
        ["already_applied"],
        fetch,
        already=set(),
        budget_tokens=10_000,
        max_files=5,
        resolve_symbol=resolve_symbol,
    )

    assert set(out) == {"pkg/ledger.py"}


def test_resolve_needs_skips_symbol_when_no_resolver() -> None:
    # Back-compat: with no resolver a non-path symbol is simply skipped.
    def fetch(path: str) -> str | None:
        return None  # the bare symbol isn't a fetchable path

    out = resolve_needs(
        ["already_applied"],
        fetch,
        already=set(),
        budget_tokens=10_000,
        max_files=5,
    )

    assert out == {}


def test_resolve_needs_prefers_path_over_symbol_resolution() -> None:
    # A `need` that fetches as a real path is used directly — the resolver,
    # which would be wasted work, is never consulted for it.
    consulted: list[str] = []

    def fetch(path: str) -> str | None:
        return "x = 1\n"

    def resolve_symbol(symbol: str) -> list[str]:
        consulted.append(symbol)
        return []

    out = resolve_needs(
        ["models.py"],
        fetch,
        already=set(),
        budget_tokens=10_000,
        max_files=5,
        resolve_symbol=resolve_symbol,
    )

    assert set(out) == {"models.py"}
    assert consulted == []  # path fetched — never fell through to symbol resolution


def test_resolve_needs_symbol_resolution_respects_caps() -> None:
    def fetch(path: str) -> str | None:
        return None if path == "thing" else "x = 1\n"  # bare symbol isn't a path

    def resolve_symbol(symbol: str) -> list[str]:
        return ["a.py", "b.py", "c.py"]  # resolver offers three candidates

    out = resolve_needs(
        ["thing"],
        fetch,
        already=set(),
        budget_tokens=10_000,
        max_files=2,  # cap below the candidate count
        resolve_symbol=resolve_symbol,
    )

    assert len(out) == 2  # symbol-resolved files honour the same file cap


class TestParseNeeds:
    """A review lens defers by putting `needs` on the findings envelope. It shares
    the auditor's coercion (one lenient normaliser, two callers), so a sloppy
    value never raises — it just yields the paths worth fetching."""

    def test_parses_needs_from_a_findings_envelope(self) -> None:
        raw = '{"findings": [], "needs": ["pkg/ledger.py", "already_applied"]}'

        assert parse_needs(raw) == ["pkg/ledger.py", "already_applied"]

    def test_tolerates_a_bare_string(self) -> None:
        assert parse_needs('{"findings": [], "needs": "pkg/ledger.py"}') == ["pkg/ledger.py"]

    def test_absent_or_empty_needs_is_no_deferral(self) -> None:
        assert parse_needs('{"findings": []}') == []
        assert parse_needs('{"findings": [], "needs": []}') == []
        assert parse_needs("[]") == []
        assert parse_needs("not json at all") == []

    def test_survives_fences_and_prose(self) -> None:
        """Same lenient extraction the findings parser uses, so a gateway without
        JSON mode (fenced output, chatty prose) still defers correctly."""
        raw = 'Sure!\n```json\n{"findings": [], "needs": ["a.py"]}\n```\nHope that helps.'

        assert parse_needs(raw) == ["a.py"]

    def test_drops_blank_and_non_string_entries(self) -> None:
        raw = '{"findings": [], "needs": ["  a.py  ", "", 7, null]}'

        assert parse_needs(raw) == ["a.py"]

    def test_coercion_is_shared_with_the_reflection_auditor(self) -> None:
        assert coerce_needs(None) == []
        assert coerce_needs("x.py") == ["x.py"]
        assert coerce_needs(["  x.py  ", 3, ""]) == ["x.py"]
        assert coerce_needs({"x.py": 1}) == []


def test_module_bounds_are_small() -> None:
    assert MAX_HOPS == 2
    assert MAX_FETCH_FILES == 5


class TestConcurrentFetching:
    """The deferral fetch is on the critical path: reflection can't start until
    every lens returns, so its round-trips are pure serial tail. They're
    independent, so they overlap — but the budget/cap accounting stays strictly
    ordered, or the same needs list could resolve differently run to run.
    """

    @staticmethod
    def _ordering_fetcher(delay: float = 0.05):
        """Records start/end order; each fetch sleeps so serial runs interleave."""
        import threading

        lock = threading.Lock()
        events: list[str] = []

        def fetch(path: str) -> str | None:
            with lock:
                events.append(f"start-{path}")
            threading.Event().wait(delay)
            with lock:
                events.append(f"end-{path}")
            return f"# {path}\nx = 1\n"

        return fetch, events

    def test_independent_fetches_overlap(self) -> None:
        fetch, events = self._ordering_fetcher()
        resolve_needs(
            ["a.py", "b.py", "c.py"], fetch, already=set(), budget_tokens=10_000, max_files=5
        )
        # Serial would be start-a, end-a, start-b, ... — at least two starts must
        # land before the first end.
        first_end = next(i for i, e in enumerate(events) if e.startswith("end-"))
        assert len([e for e in events[:first_end] if e.startswith("start-")]) >= 2

    def test_budget_still_applied_in_needs_order(self) -> None:
        """Concurrency must not reorder budget accounting: the first-named file
        takes the budget, the second is skipped — the same either way."""
        big = "\n".join(f"line {i} of a reasonably large file" for i in range(400)) + "\n"

        def fetch(path: str) -> str | None:
            return big

        out = resolve_needs(
            ["first.py", "second.py"],
            fetch,
            already=set(),
            budget_tokens=count_tokens(big) + 10,
            max_files=5,
        )
        assert list(out) == ["first.py"]

    def test_cap_bounds_the_number_of_fetches(self) -> None:
        """Overlapping must not fetch every candidate when the cap is far lower —
        an unbounded prefetch would burn API calls on files it then discards."""
        calls: list[str] = []

        def fetch(path: str) -> str | None:
            calls.append(path)
            return "x = 1\n"

        out = resolve_needs(
            [f"f{i}.py" for i in range(40)],
            fetch,
            already=set(),
            budget_tokens=10_000,
            max_files=2,
        )
        assert len(out) == 2
        assert len(calls) <= 10, f"fetched {len(calls)} files for a cap of 2"


def test_symbol_candidates_are_also_bounded_by_the_cap() -> None:
    """The symbol branch must respect `max_files` too. `SymbolResolver` is an
    injected callable — ast-grep caps itself at 3 candidates, but that is the
    default's detail, not the contract — so prefetching every definition a
    resolver returns would burn fetches the cap can never accept."""
    calls: list[str] = []

    def fetch(path: str) -> str | None:
        calls.append(path)
        return "x = 1\n" if path.endswith(".py") else None

    def resolve_symbol(symbol: str) -> list[str]:
        return [f"def{i}.py" for i in range(30)]

    out = resolve_needs(
        ["a_symbol"],
        fetch,
        already=set(),
        budget_tokens=10_000,
        max_files=2,
        resolve_symbol=resolve_symbol,
    )

    assert len(out) == 2
    # 1 direct attempt on the symbol name + at most a cap-sized wave of candidates.
    assert len(calls) <= 5, f"fetched {len(calls)} candidates for a cap of 2"


def test_symbol_candidates_keep_trying_past_an_unfetchable_one() -> None:
    """Bounding the burst must not stop the search early: if the first candidate
    can't be fetched, later ones are still tried, as they were when each fetch
    blocked."""

    def fetch(path: str) -> str | None:
        return None if path in ("sym", "miss.py") else "x = 1\n"

    def resolve_symbol(symbol: str) -> list[str]:
        return ["miss.py", "real.py"]

    out = resolve_needs(
        ["sym"],
        fetch,
        already=set(),
        budget_tokens=10_000,
        max_files=1,
        resolve_symbol=resolve_symbol,
    )

    assert list(out) == ["real.py"]
