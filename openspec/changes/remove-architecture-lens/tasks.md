## 1. Remove the lens

- [x] 1.1 Update tests first (`test_preset.py`, `test_prompt.py`, `test_conflicts.py`, `test_reflect.py`, `test_severity_ceiling.py`, `test_engine.py`, `tests/evals/test_fixtures.py`, `tests/cli/test_mcp.py`, `tests/docs/test_homepage.py`, `tests/providers/test_provider_matrix.py`) and verify they fail for the expected reason
- [x] 1.2 Remove the `architecture` category, prompt section, example, reflection carve-out, severity ceiling, ladder rung and opposed pair, then verify `uv run pytest tests/engine tests/core` passes
- [x] 1.3 Add the retired-name handling for `categories` with a test that a config listing `architecture` loads, drops it and warns
- [x] 1.4 Delete `evals/fixtures/architecture/` and verify `uv run pytest tests/evals` passes

## 2. Reduce finding pressure

- [x] 2.1 Add failing `test_prompt.py` tests for the empty-result and worth-raising rules, then update the shared rules and verify they pass
- [x] 2.2 Add a failing `test_reflect.py` test for the worth-the-churn drop rule, then update the reflection prompt and verify it passes
- [x] 2.3 Add a clean-change eval fixture whose `forbidden` list names tempting nits, and verify `uv run pytest tests/evals` passes

## 3. Docs and specs

- [x] 3.1 Update `AGENTS.md`, `ATTRIBUTION.md`, `README.md`, `DEVELOPMENT.md`, `action.yml` and `docs/` for four calls and nine categories, and verify `uv run pytest tests/docs` passes
- [x] 3.2 Regenerate `docs/reference/config.md`, `docs/llms*.txt` and the `ReviewConfig` snapshot, and verify the freshness tests pass
- [x] 3.3 Fold the delta specs into `openspec/specs`, remove the `prompt.architecture` anchor, and verify `uv run pytest tests/specs -q` and `openspec validate --specs` pass

## 4. Verify

- [x] 4.1 Run the full `uv run pytest`, `ruff` and type checks and confirm a clean result
- [ ] 4.2 Run `python -m evals.run` on the clean-change fixture and a recall fixture with a live model, and record the result on the PR
