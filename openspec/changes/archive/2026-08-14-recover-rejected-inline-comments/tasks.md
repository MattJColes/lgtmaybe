## 1. Acceptance Coverage

- [x] 1.1 Add a rerun test where one inline comment returns 422, a later comment
  succeeds, and the rejected finding appears in the updated review body.
- [x] 1.2 Add coverage that logs sanitized GitHub validation details.
- [x] 1.3 Add coverage that non-422 responses and fallback-body failures still
  fail the review.

## 2. GitHub Posting

- [x] 2.1 Keep each rerun inline payload associated with its source finding.
- [x] 2.2 Collect 422 rejections, continue posting, and update the review body
  with rejected findings through the existing demoted renderer.
- [x] 2.3 Log the rejected position and parsed GitHub validation response without
  secrets or model-authored prose.

## 3. Verification

- [x] 3.1 Run focused GitHub posting and CLI integration tests.
- [x] 3.2 Run the full pytest suite, Ruff, mypy, and spec-anchor checks. The full
  run reached 2,083 passes; its 16 call-count failures occur because the active
  OpenSpec proposals enable the spec lens in tests that assume no active spec.
- [x] 3.3 Validate the OpenSpec change strictly.
