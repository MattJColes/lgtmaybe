## 1. Acceptance Coverage

- [x] 1.1 Add a gateway test where an `ahead` comparison contains a merge
  commit and returns no incremental diff.
- [x] 1.2 Keep the existing linear `ahead` comparison behavior covered.

## 2. GitHub Incremental Review

- [x] 2.1 Reuse the comparison metadata request to detect multiple-parent
  commits and fall back to a full review.
- [x] 2.2 Keep diverged, identical, and API-failure fallbacks unchanged.

## 3. Verification

- [x] 3.1 Run focused gateway and CLI incremental-review tests.
- [x] 3.2 Isolate tests from the developer checkout so an active OpenSpec
  proposal cannot alter unrelated engine call counts.
- [x] 3.3 Validate the OpenSpec change strictly and inspect the final diff.
- [x] 3.4 Run the full pytest suite, Ruff, mypy, and spec-anchor checks.
