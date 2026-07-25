## 1. Regression Coverage

- [x] 1.1 Add a structural test requiring concurrency to live on the guarded
  review job
- [x] 1.2 Run the focused test and confirm it fails against the current workflow

## 2. Workflow Fix

- [x] 2.1 Move the existing per-PR concurrency mapping from workflow scope to
  `jobs.review`
- [x] 2.2 Re-run the focused workflow tests and confirm they pass

## 3. Validation

- [x] 3.1 Validate the OpenSpec change and living-spec anchors
- [x] 3.2 Run the relevant workflow tests and repository quality gates
