## 1. Regression Coverage

- [x] 1.1 Update the structural Action test to require `client-id` and reject
  the deprecated `app-id` key
- [x] 1.2 Run the focused test and confirm it fails against the current Action

## 2. Action Fix

- [x] 2.1 Forward the existing lgtmaybe `app_id` through the upstream
  `client-id` key
- [x] 2.2 Re-run the focused Action tests and confirm they pass

## 3. Validation

- [x] 3.1 Validate the OpenSpec change and living-spec anchors
- [x] 3.2 Run the repository quality gates
