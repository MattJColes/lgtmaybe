## 1. Acceptance Coverage

- [x] 1.1 Add `tests/engine/test_truncation_split.py`: a lens whose first call
  truncates splits and reviews the halves
- [x] 1.2 Cover the salvage — findings completed before the cut survive the split
  and are stamped like any other finding
- [x] 1.3 Cover the terminal case — a piece that truncates again reports
  `max_tokens` rather than recursing
- [x] 1.4 Cover the notices — an unsplittable truncation and a failed piece both
  keep the incomplete-results notice firing
- [x] 1.5 Extend the provider truncation tests: the ceiling is named as
  `max_tokens`, reasoning tokens are reported when the route gives them, and the
  cut-off body travels with the failure

## 2. Engine

- [x] 2.1 Rename `on_wall_timeout` to `on_oversized` and fire it for
  `ProviderTruncated` as well as `ProviderWallTimeout`
- [x] 2.2 Salvage the findings completed before the cut, returning them with the
  failure reason so a partial lens still counts as failed
- [x] 2.3 Reword the shrink notice for both triggers, naming `max_tokens`

## 3. Provider

- [x] 3.1 Carry the truncated body on `ProviderTruncated`
- [x] 3.2 Name `max_tokens` as the ceiling and report reasoning tokens where the
  route exposes them
- [x] 3.3 Leave `_is_permanent` unchanged, with a comment explaining why the
  engine's split and the adapter's refusal to retry are not in tension

## 4. Documentation

- [x] 4.1 Update `docs/how-to/reduce-review-cost.md` and
  `docs/explanation/trust-and-cost.md`
- [x] 4.2 Regenerate `docs/llms.txt`

## 5. Verification

- [x] 5.1 Run the new truncation and existing timeout split suites
- [x] 5.2 Run the complete suite, lint, format, and type checks
- [x] 5.3 Run spec-anchor checks and validate the OpenSpec specs
