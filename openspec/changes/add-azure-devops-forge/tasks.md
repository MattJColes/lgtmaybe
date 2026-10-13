## 1. Locator And Tokens

- [x] 1.1 Add `Forge.azure_devops` and parse both URL shapes, the short form, and the Server refusal; verify `tests/core/test_forge.py`
- [x] 1.2 Add `token_env_vars` with `AZURE_DEVOPS_TOKEN` then `SYSTEM_ACCESSTOKEN`, and name every variable in the missing-token error; verify `tests/core/test_forge.py` and `tests/azure_devops/test_pipelines_entrypoint.py`

## 2. Diff Rendering

- [x] 2.1 Add `core/diffbuild.py` (`file_patch`, `binary_file_patch`, `has_unsafe_path_chars`); verify `tests/core/test_diffbuild.py` round-trips edits, adds, deletes and renames through the shared parser and keeps hostile content inside hunks

## 3. Gateway

- [x] 3.1 `get_pr_context`: metadata, intent, latest iteration against the merge base, concurrent Items API fetches, binary and size caps, the file cap as `DiffUnavailable`; verify `tests/azure_devops/test_gateway.py::TestGetPRContext`
- [x] 3.2 Positioned finding threads on both sides, demotion when unanchored or rejected; verify `TestPositions`
- [x] 3.3 Summary, description and diagram upserts with disjoint markers and the 403 fallback; verify `TestSummary`
- [x] 3.4 De-duplication against live, non-fixed threads, one for one; verify `TestDedupe`
- [x] 3.5 Basic and bearer auth, `api-version` on every request, no token in URLs or logs; verify `TestTokenHandling`
- [x] 3.6 Declare only built capabilities; verify `TestCapabilities`

## 4. Entrypoint

- [x] 4.1 Register the builder in `cli._GATEWAY_BUILDERS`; verify `tests/core/test_forge.py::TestGatewayRegistry`
- [x] 4.2 Add `pr_url_from_pipelines_env` and the `azure-pipelines` command; verify `tests/azure_devops/test_pipelines_entrypoint.py`

## 5. Docs And Specs

- [x] 5.1 Add `docs/how-to/review-on-azure-devops.md` and `examples/azure-devops/azure-pipelines.yml`; add them to `mkdocs.yml`, the README host tables, and the explanation capability tables
- [x] 5.2 Regenerate `docs/llms.txt` and `docs/llms-full.txt`
- [x] 5.3 Add the `azure-devops-posting` living spec with anchors; verify `uv run pytest tests/specs -q`
