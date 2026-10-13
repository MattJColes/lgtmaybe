# Tasks

TDD throughout: each task's test is written first and seen failing before the
code that passes it.

## 1. Incremental review

- [x] 1.1 `compare_diff` reassembles per-file patches and refuses every untrusted shape; verify `tests/gitlab/test_incremental.py::TestCompareDiff`
- [x] 1.2 Watermark stamped only on success, kept on failure, forged markers stripped; verify `TestWatermarkStamping`
- [x] 1.3 Watermark read only from our own note; verify `TestWatermarkReading` and `TestCompletedHead`
- [x] 1.4 Diagram completion stamp and preservation; verify `TestDiagramCompletionMarker`
- [x] 1.5 Derived outdated signal and increment-scoped resolution; verify `TestOutdatedSignal` and `TestIncrementalScope`
- [x] 1.6 End to end through `run_review`; verify `tests/gitlab/test_incremental_flow.py`

## 2. Feedback

- [x] 2.1 `list_downvoted_fingerprints` from award emoji with membership check; verify `tests/gitlab/test_feedback.py`

## 3. Entrypoint

- [x] 3.1 `gitlab-ci` auto-incremental default and override; verify `tests/gitlab/test_ci_entrypoint.py::TestGitLabCIIncrementalDefault`

## 4. Docs and verification

- [x] 4.1 README, GitLab how-to, architecture and what-gets-reviewed tables, CLAUDE.md Forges paragraph
- [x] 4.2 Living specs updated; `uv run pytest tests/specs -q`, ruff, mypy, full suite
