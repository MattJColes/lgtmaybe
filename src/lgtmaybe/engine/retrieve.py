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

from collections.abc import Callable

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

    def accept(path: str) -> None:
        """Fetch + redact *path* into *out*, respecting the token + file caps."""
        nonlocal used
        if path in already or path in seen or path in out:
            return
        seen.add(path)
        raw = fetch_file(path)
        if not raw:
            return
        text = redact(raw)
        cost = count_tokens(text)
        if used + cost > budget_tokens:
            return  # would blow the per-hop budget — skip this file
        out[path] = text
        used += cost

    for need in needs:
        if len(out) >= max_files:
            break
        before = len(out)
        accept(need)
        if len(out) > before or resolve_symbol is None:
            continue
        # Not a fetchable path — try resolving it as a symbol to its defining
        # file(s), then fetch those through the same read-only boundary.
        for candidate in resolve_symbol(need):
            if len(out) >= max_files:
                break
            accept(candidate)

    return out
