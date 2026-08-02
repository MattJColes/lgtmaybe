## Why

lgtmaybe has exactly one repo-level configuration. A monorepo cannot say "`payments/**` is strict, `tests/**` is lenient, and read `ARCHITECTURE.md` before reviewing `src/**`" — every file gets the same lenses, the same emphasis, and no background reading. Teams whose risk profile varies by directory either over-tune the whole repo to its riskiest corner or accept noise everywhere. Competing reviewers (Cursor Bugbot's `.cursor/BUGBOT.md` walk-up, Greptile's `directoryRules` + `customContext`) already scope both.

## What Changes

- Add `ReviewConfig.directory_rules`: a list of `DirectoryRule` (path globs, `instructions`, `context_files`). YAML-only, like `finding_rules` and `extra_lenses`.
- Add `engine/directory.py` — match rules to a batch's files, read the named context files from the checked-out workspace, and render one prompt block.
- Join that block into each batch's existing cacheable prefix, so the cross-batch system preamble and the cache-breakpoint contract are untouched.
- Document the feature as a how-to and a `.lgtmaybe.yml` field reference.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `prompt-and-lenses`: instructions and reference files become scopeable to part of the repo, delivered per batch through the same cacheable prefix.

## Impact

The change touches `core/models.py`, the new `engine/directory.py`, the engine's batch loop and its two prompt shapes, the `prompt-and-lenses` living spec, and the docs. It adds no dependency, no CLI flag, and no Action input; a repo with no `directory_rules` reads no workspace file and produces a byte-identical prompt.
