## 1. Acceptance Test

- [x] 1.1 Add a test deriving the expected GHCR major from `pyproject.toml` and
  confirm it fails against the v0 Action default.

## 2. Distribution Alignment

- [x] 2.1 Change the Action's default container image to v1.
- [x] 2.2 Update supplied workflows and public documentation from v0 to v1.
- [x] 2.3 Remove the consumed `release-as: 1.0.0` override.

## 3. Verification and Release

- [x] 3.1 Regenerate derived documentation and validate the OpenSpec change.
- [x] 3.2 Run focused tests, the full test/lint/type/spec gates, and the local
  Action image smoke path.
- [x] 3.3 Review the final diff and publish the fix PR.
