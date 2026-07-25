## 1. Reduce the routine CI matrix

- [x] 1.1 Add a workflow contract test that expects exactly Ubuntu and Windows
  test jobs using Python 3.11, then run it to confirm the current matrix fails.
- [x] 1.2 Replace the Python-version matrix with the two-platform matrix and a
  fixed Python 3.11 setup, preserving every existing gate step and the stable
  `check` job.
- [x] 1.3 Run the workflow contract test and confirm it passes.

## 2. Keep durable guidance in sync

- [x] 2.1 Update the anchored `windows-distribution` requirement and the CI
  matrix description in `CLAUDE.md`.
- [x] 2.2 Run the targeted workflow tests, `uv run pytest tests/specs -q`, and
  OpenSpec validation.
