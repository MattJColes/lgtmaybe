## Why

Review noise comes from how the prompts frame the job. Every lens is told to look for one kind of problem, and an empty answer is framed as the rare exception ("only when there are genuinely no issues"). Nothing weighs a fix against the churn it causes, so a lens with nothing real to say proposes a trivial one. The architecture lens is the worst case: it runs on every review and asks a model to speculate about future change.

## What Changes

- **BREAKING**: remove the built-in `architecture` lens. The `fast` preset becomes four calls covering nine everyday categories. A required-lens gate that reads the hidden lenses marker and expects `architecture` must drop it.
- A config that still lists `architecture` under `categories` keeps working: the entry is ignored with a warning.
- Make an empty result the expected answer in the shared review rules, and require that a finding is something a competent engineer would change before merging.
- Tell every lens not to raise a change that only swaps one acceptable form for another.
- Give reflection a matching drop rule.
- Add an eval fixture of a clean change, so the precision of these rules is measurable.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `core-contracts`: the built-in lens list and preset fan-out lose `architecture`; a removed category in config is ignored with a warning.
- `prompt-and-lenses`: the fast preset is four lenses, the architecture requirement goes, and a new requirement makes silence the expected outcome.
- `review-pipeline`: the fan-out scenarios name four calls.
- `finding-quality`: reflection loses the architecture carve-out and gains the worth-the-churn drop rule.

## Impact

- Code: `core/models.py`, `engine/prompt.py`, `engine/lenses.py`, `engine/reflect.py`, `engine/conflicts.py`, `engine/severity.py`, `cli/commands.py`, `action.yml`.
- Evals: delete `evals/fixtures/architecture/`, add a clean-change fixture.
- Generated: `tests/snapshots/ReviewConfig.json`, `docs/reference/config.md`, `docs/llms*.txt`.
- Docs: every page that says "five calls" or "ten categories", `ATTRIBUTION.md`, `AGENTS.md`.
- The shared prompt prefix changes, so prompt caches miss once after release.
