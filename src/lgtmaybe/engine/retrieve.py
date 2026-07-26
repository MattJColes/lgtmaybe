"""Bounded, read-only retrieval for deferred reflection verdicts.

When the auditor (``engine/reflect.py``) would drop a finding ONLY because it
cannot see a file or definition the finding depends on, it DEFERS instead of
dropping: it names the file path(s) it needs. The engine fetches that text
**read-only** (never a checkout, never executing PR code — fork-safe), redacts
it, and the auditor re-judges with it in context.

This module is the fetch half: ``resolve_needs`` turns a list of requested paths
into a ``{path: redacted_text}`` map, bounded by a token budget and a file count
so a malicious or runaway ``needs`` list can't pull the whole repo. The recheck
loop and its hop cap live in ``reflect.py``; the hard stops it relies on
(``MAX_HOPS``, ``MAX_FETCH_FILES``) are defined here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor

from .astgrep import SymbolResolver
from .compress import count_tokens
from .redact import redact

# A read-only file reader: path → file text, or None when it can't be fetched
# (missing, deleted, an API/disk error). The ONLY I/O the resolver performs — it
# is injected, so there is no checkout/exec path for an attacker diff to reach.
FileFetcher = Callable[[str], "str | None"]

# Bounded escalation. At most MAX_HOPS recheck rounds (the auditor re-judges with
# newly fetched files), and at most MAX_FETCH_FILES files pulled in total — the
# hard stops that keep a deferral from looping or fetching the whole repo.
MAX_HOPS = 2
MAX_FETCH_FILES = 5

# Concurrent read-only fetches per wave. The deferral fetch sits on the review's
# serial tail — reflection cannot begin until every lens has returned — so these
# round-trips are pure added wall clock, and they are independent of each other.
# Bounded so a long `needs` list can't open a connection per entry.
_FETCH_WORKERS = 8


def resolve_needs(
    needs: list[str],
    fetch_file: FileFetcher,
    *,
    already: set[str],
    budget_tokens: int,
    max_files: int,
    resolve_symbol: SymbolResolver | None = None,
) -> dict[str, str]:
    """Fetch the requested *needs* read-only, redacted, within budget.

    For each requested entry not already grounded (``already``), call
    ``fetch_file`` (the one injected read-only I/O path), redact the text, and
    accept it while the running token total stays under ``budget_tokens`` and the
    accepted count stays under ``max_files``. Entries that resolve to ``None``/empty,
    that don't fit the remaining budget, or that exceed the file cap are skipped.

    A ``needs`` entry that isn't a fetchable path (the auditor named a SYMBOL, not a
    file) used to dead-end here. When ``resolve_symbol`` is supplied (ast-grep,
    ``engine/astgrep.py``), such an entry is mapped to the file(s) that DEFINE the
    symbol and those are fetched through the same ``fetch_file`` — so the symbol
    path reuses the one audited, read-only I/O boundary (and its redaction), never a
    second one. De-duplicates so a repeated path or symbol→file costs one fetch.
    Returns ``{path: redacted_text}``.
    """
    out: dict[str, str] = {}
    used = 0
    seen: set[str] = set()
    fetched: dict[str, str | None] = {}

    def prefetch(paths: Iterable[str]) -> None:
        """Fetch *paths* concurrently into the raw cache, once each.

        Only the I/O is overlapped. Acceptance below still walks `needs` in
        order, so the token budget and file cap allocate exactly as they did
        when each fetch blocked — the same `needs` list must always resolve to
        the same files, whatever order the responses happen to land in.
        """
        todo = [p for p in dict.fromkeys(paths) if p not in seen and p not in already]
        if not todo:
            return
        seen.update(todo)
        with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(todo))) as pool:
            fetched.update(zip(todo, pool.map(fetch_file, todo), strict=True))

    def accept(path: str) -> bool:
        """Take an already-fetched *path* into *out*, respecting the caps.

        False when it was not fetched, is already in, or would blow the budget —
        the same three cases that used to leave `out` unchanged.
        """
        nonlocal used
        if path in out:
            return False
        raw = fetched.get(path)
        if not raw:
            return False
        text = redact(raw)
        cost = count_tokens(text)
        if used + cost > budget_tokens:
            return False  # would blow the per-hop budget — skip this file
        out[path] = text
        used += cost
        return True

    # Fetch in waves of at most `max_files`, so a long needs list never pulls
    # (and pays for) far more files than the cap can ever accept.
    pending = list(needs)
    while pending and len(out) < max_files:
        wave, pending = pending[:max_files], pending[max_files:]
        prefetch(wave)
        for need in wave:
            if len(out) >= max_files:
                break
            if accept(need) or resolve_symbol is None:
                continue
            # Not a fetchable path — try resolving it as a symbol to its defining
            # file(s), then fetch those through the same read-only boundary.
            # ast-grep is a local scan, so this stays on the ordered walk; only
            # its resulting fetches are batched.
            candidates = resolve_symbol(need)
            prefetch(candidates)
            for candidate in candidates:
                if len(out) >= max_files:
                    break
                accept(candidate)

    return out
