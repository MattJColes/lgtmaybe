## ADDED Requirements

### Requirement: A lens may defer once for bounded read-only context

With `mid_review_retrieval` on, a lens that answers `needs` SHALL be re-run once
with those paths/symbols fetched read-only (redacted, capped at
`MAX_FETCH_FILES` files and a quarter of `max_input_tokens`) appended to its own
uncached block, never the shared prefix, and the two calls' findings merged.
Off (the default) `needs` is never parsed and every prompt is byte-identical.
<!-- anchor: engine.mid-review-retrieval -->

#### Scenario: a lens asks to read a file
- **WHEN** a lens answers `{"findings": [...], "needs": ["pkg/ledger.py"]}`
- **THEN** that file is fetched read-only and the lens is re-run once with it,
  and both calls' findings are kept and deduped

#### Scenario: the re-run asks again
- **WHEN** the re-run also answers with `needs`
- **THEN** it is ignored — one hop per (batch, lens), so at most one extra call

#### Scenario: nothing readable comes back
- **WHEN** every requested path/symbol resolves to nothing or exceeds the budget
- **THEN** the first call's findings stand, and the call is not a failure

#### Scenario: the deferral arrives past a ceiling
- **WHEN** the wall-clock deadline or token budget has passed when a lens defers
- **THEN** nothing is fetched, the first call's findings stand, and the run
  reports the existing incomplete-results notice

#### Scenario: retrieval is off or nothing can fetch
- **WHEN** `mid_review_retrieval` is off, or no read-only reader is injected
- **THEN** no lens is asked for `needs` and none is ever re-run
