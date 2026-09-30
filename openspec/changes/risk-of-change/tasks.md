# Tasks

TDD throughout: each task's test is written first and seen failing before the
code that passes it.

## 1. Config Surface

- [x] 1.1 Add `RiskConfig(enabled=True, core_paths=[])` and `ReviewConfig.risk`; verify tests in `tests/test_models.py` cover the defaults and `extra=forbid` rejecting an unknown key
- [x] 1.2 Load `risk:` from `.lgtmaybe.yml`; verify `tests/config/test_loader.py` round-trips `enabled` and `core_paths`
- [x] 1.3 Add `--risk/--no-risk` to the CLI and the `risk` input to `action.yml` + `INPUT_RISK` wiring; verify CLI tests show the flag and input both reach `cfg.risk.enabled`, and an empty input keeps the default
- [x] 1.4 Regenerate `docs/reference/config.md` with `docs/generate_reference.py` and add the input to the Action inputs docs; verify `tests/docs/test_reference_fresh.py` passes

## 2. Blast Radius Scan

- [x] 2.1 Python import extraction and resolution (absolute, `from` imports, relative imports, `src/` layout, `__init__.py`) over a `tmp_path` workspace; verify new tests in `tests/engine/test_risk.py` count distinct importers and exclude the file itself
- [x] 2.2 TS/JS relative imports, `export … from`, `require()` and `import()` with extension and `/index.*` probing, bare specifiers ignored; verify tests for each form
- [x] 2.3 Unassessed paths: no workspace, scan timeout (inject a short limit), unsupported language, new file not reported unassessed; verify tests for each spec scenario under "Unmeasured blast radius is named, never scored low"
- [x] 2.4 Run the scan through the static-analysis subprocess helpers (scrubbed env, hard timeout) and carry the result on `PRContext.risk` so a run scans once; verify a test that a context already carrying a verdict is not re-scanned, and one that the child env carries no provider keys

## 3. Level Rules

- [x] 3.1 `assess_risk` combining blast radius, `core_paths`, High Impact path signals, size and missing tests into a level with ranked reasons; verify tests for every scenario under "Factors combine into a level by fixed rules" and "Blast radius counts the importers of each changed file"
- [x] 3.2 Test and doc path classification; verify table-driven tests for each pattern in design D6, including negatives (`contest.py`, `latest/`)
- [x] 3.3 Any exception inside the assessment yields an unavailable verdict; verify a test where the scan raises and the review still completes

## 4. Summary Line And Marker

- [x] 4.1 Assess in `run_review` before incremental, triage and `max_files` scoping; verify a test where an incremental re-review reports the same level and reasons as the full review
- [x] 4.2 Add the `risk <level> (<reason>)` segment to the default summary line, the `{risk}` placeholder to `summary_template`, and `risk unavailable` for a failed assessment; verify tests in `tests/engine/test_engine.py` for the default, templated, LGTM and unavailable shapes
- [x] 4.3 Append `<!-- lgtmaybe-risk:<level> -->` on every summary shape, never when unavailable; verify a test that the marker family is disjoint from the summary, finding, reviewed, incomplete and lenses markers
- [x] 4.4 Confirm the verdict renders on GitLab and Gitea summaries unchanged (engine-level, no adapter work); verify one test per gateway that the posted summary carries the marker
- [x] 4.5 Document the summary segment, marker and how to gate on it in a new `docs/explanation/risk-of-change.md` (with a meta description), link it from `docs/how-to/configure-lgtmaybe-yml.md`, and regenerate `docs/llms*.txt` with `docs/generate_llms_txt.py`; verify `uv run --group docs mkdocs build --strict` passes

## 5. Overview Section

- [ ] 5.1 `render_risk_section` headed `### **Risk of Change: <Level>**` listing every reason; verify tests for low, high and critical with unassessed files
- [ ] 5.2 Place it in `build_overview` after the description and before High Impact Areas, gated on `risk.enabled` and independent of `high_impact`; verify tests in `tests/engine/test_overview.py` for order and for `high_impact` off
- [ ] 5.3 `lgtmaybe diagram` prints the same section; verify a CLI test on its output
- [ ] 5.4 Update CLAUDE.md (a Risk of Change entry beside the change overview) and the docs homepage overview example if it shows the section order; verify the homepage spec tests in `tests/docs/` still pass

## 6. Labels

- [ ] 6.1 Add the `risk/` label family to `engine/labels.py`, exactly one level at a time, skipped when risk is disabled; verify tests in `tests/engine/test_labels.py`
- [ ] 6.2 Reconciliation replaces a stale `risk/` label and leaves it alone when risk is disabled; verify GitHub adapter tests for the three scenarios in the modified "Labels touch only our own families" requirement, plus the same for any other gateway implementing `SupportsLabels`

## 7. Integration

- [ ] 7.1 Add `openspec/specs/risk-of-change/anchors.yml` binding each anchor id to its function, and update the `github.labels` section; verify `uv run pytest tests/specs -q` passes
- [ ] 7.2 Verify `npx -y @fission-ai/openspec@latest validate --specs` and `validate risk-of-change --strict` pass
- [ ] 7.3 Run the full gate (`uv run pytest`, ruff, mypy, `uv lock --check`); verify all green
- [ ] 7.4 Run `lgtmaybe review` locally on this repo against a branch touching `src/lgtmaybe/core/models.py` and on a docs-only branch; verify the first reports `high` or above naming the importer count and the second reports `low` with "docs and tests only"
