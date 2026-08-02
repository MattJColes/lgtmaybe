## Why

lgtmaybe already has bounded, read-only retrieval — but only the reflection
auditor may use it (`engine/retrieve.py::resolve_needs`, plus the ast-grep
`SymbolResolver`). Reflection runs after the findings exist, so its retrieval can
only ever *rescue a finding already made*. It cannot recover a finding the lens
never made.

And the lens is explicitly told not to make those. The shared review rules
(`engine/prompt.py`) say the diff is only a slice of the codebase, so a claim
resting on unshown code must be hedged, downgraded, or omitted. That is the right
default for precision — the `cross-file-fp` eval fixture exists because
over-confident cross-file claims were a real failure mode — but it throws away
every finding whose evidence is one file away. A unit mismatch between a caller
in the diff and a helper it imports is invisible by construction.

A lens that can investigate can find what it would otherwise stay silent about.
That closes most of the multi-hop-investigation gap against an indexed reviewer
(Greptile) without building or maintaining an index.

## What Changes

- `ReviewResult` gains an optional `needs: list[str]`, so a lens can answer
  "here are my findings, AND here is the code I must read to decide" in one
  structured response.
- With `ReviewConfig.mid_review_retrieval` on (default **off**), the shared
  preamble (and the legacy per-lens system prompts) asks for that deferral,
  bounded to one round.
- On a deferral, the engine fetches the named paths/symbols through the SAME
  read-only, redacting boundary reflection already uses — never a checkout — and
  re-runs that one lens with the text appended to its own uncached block.
- The two calls' findings are concatenated and left to the existing dedupe, so a
  deferral can only add findings, never lose the ones already made.
- `reflect._coerce_needs` moves to `parse.coerce_needs`, so a lens deferral and
  an auditor deferral are read by one lenient coercion.
- A new `cross-file-recall` eval fixture measures the recall this buys, and
  `python -m evals.run --mid-review-retrieval` A/Bs it against a live model.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `review-pipeline`: a lens may defer once for bounded read-only context, with
  the fetch bounded by hop, file count, token budget, and both soft ceilings.
- `core-contracts`: the findings envelope carries an optional `needs` deferral.
- `prompt-and-lenses`: the deferral ask is gated on `mid_review_retrieval` and
  adds zero bytes to any prompt when off.

## Impact

Off by default, this changes nothing: no prompt moves a byte (the shared prefix
is a cache entry, so that matters), `needs` is never parsed, and no fetch
happens. On, the worst case is one extra model call per (batch, lens) plus the
fetched file text — real money — in exchange for cross-file findings the reviewer
currently refuses to make. That trade is unmeasured on a live model, which is why
it ships off and with an eval fixture and an A/B procedure rather than a default
flip. It reuses the existing fetcher wiring in `cli.build_review_context` and the
local CLI, so there is no new I/O path and no new fork-safety surface.
