## 1. Acceptance Tests

- [x] 1.1 `tests/engine/test_retrieve.py::TestParseNeeds` — a `needs` list parses
  off a findings envelope, a bare string is tolerated, and the coercion is the
  one the reflection auditor uses.
- [x] 1.2 `tests/engine/test_mid_review_retrieval.py` — a deferring lens is
  re-run with the fetched file; the re-run never defers again; a deferral is
  ignored with the feature off or with no fetcher.
- [x] 1.3 Same suite — the fetched context rides the lens block, not the cached
  prefix; it is redacted and neutralised.
- [x] 1.4 Same suite — nothing fetched keeps the first call's findings; a
  deferral past the deadline is skipped and noticed; both calls' findings merge
  and dedupe.
- [x] 1.5 `tests/engine/test_prompt.py` — the preamble (and every legacy system
  prompt) is byte-identical with retrieval off, and asks for `needs` when on.

## 2. Implementation

- [x] 2.1 `ReviewResult.needs` (back-compat default, `extra="forbid"` kept);
  `parse.parse_needs` + `parse.coerce_needs`, with `reflect._coerce_needs`
  moved there so both deferral paths share one coercion.
- [x] 2.2 `prompt.retrieval_rules` gating the ask on the split preamble and all
  four legacy system prompts.
- [x] 2.3 `engine._review_with_context` + the `on_needs` callback on
  `_complete_lens`, with `_skip_reason` and `_lens_messages` extracted so the
  first call and the re-run share their ceilings and message shape.
- [x] 2.4 `injection.wrap_context` — its own neutralised marker family for the
  fetched files.
- [x] 2.5 `ReviewConfig.mid_review_retrieval` wired end to end: CLI flag,
  `action.yml` input, `INPUT_MID_REVIEW_RETRIEVAL`, and the action-input reader.

## 3. Measurement

- [x] 3.1 `evals/fixtures/cross-file-recall` — a real bug visible only in an
  unshown file, plus a forbidden trap the same files refute.
- [x] 3.2 `python -m evals.run --mid-review-retrieval` for the A/B; procedure
  recorded in `design.md`.
- [ ] 3.3 Run the A/B against a live model before considering a default flip.

## 4. Verification

- [x] 4.1 Docs: architecture (pipeline), reduce-review-cost (it raises cost),
  configure-lgtmaybe-yml (field reference + contents), both generators re-run.
- [x] 4.2 Living spec + anchor: `engine.mid-review-retrieval`.
- [x] 4.3 Full gate: pytest, ruff format/check, mypy, spec anchors, OpenSpec
  validate.
