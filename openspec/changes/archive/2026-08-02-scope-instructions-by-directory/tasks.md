## 1. Acceptance Tests

- [x] 1.1 Add failing model tests: a rule defaults to every path, and `extra="forbid"` rejects unknown keys.
- [x] 1.2 Add failing `tests/engine/test_directory.py`: rules match only batches touching their paths, context files are redacted, loading stops at the token budget, a path outside the root is ignored, and context never comes from the PR head.
- [x] 1.3 Add a failing engine test that directory instructions reach only the matching batch.
- [x] 1.4 Add failing prompt-split tests: the shared preamble is byte-identical with and without rules, and the block rides the prefix message, not the lens block.
- [x] 1.5 Add a failing loader test that `directory_rules` load from the repo config.

## 2. Implementation

- [x] 2.1 Add `DirectoryRule` and `ReviewConfig.directory_rules` (YAML-only, no CLI flag or Action input).
- [x] 2.2 Add `engine/directory.py`: `rules_for` (reusing `passes_path_filters`), `load_context_files` (reusing `retrieve.resolve_needs` with a workspace fetcher), and `build_directory_block`.
- [x] 2.3 Load the context texts once before the batch loop, match rules per batch, and join the block into the existing per-batch prefix in both prompt shapes.

## 3. Specification and Documentation

- [x] 3.1 Add the anchored `prompt-and-lenses` requirement and its `anchors.yml` rule.
- [x] 3.2 Add the how-to guide, register it in the mkdocs nav, and add the `.lgtmaybe.yml` field reference block.
- [x] 3.3 Regenerate the schema snapshots, `docs/reference/config.md`, and `docs/llms*.txt`.

## 4. Verification

- [x] 4.1 Run the full gate: pytest, ruff format/check, mypy, spec tests, and `openspec validate --specs`.
