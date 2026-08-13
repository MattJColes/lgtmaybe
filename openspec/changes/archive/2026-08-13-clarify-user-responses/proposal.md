## Why

lgtmaybe's findings and conversational answers are technically accurate but do not consistently make the reader's next action obvious. Applying a small, task-oriented response contract will make review feedback faster to understand and act on without adding fields, configuration, or visual noise.

## What Changes

- Make review finding titles lead with the concrete corrective action when one is known.
- Make finding bodies state the cause and observable impact directly, without preamble, repetition, or closing pleasantries.
- Make `/ask` answers and finding-thread replies lead with the answer, use numbered steps only for genuinely multi-step work, and end with one concrete next action only when action remains.
- Keep existing structured-output schemas, severity semantics, GitHub rendering, and CLI formats unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `prompt-and-lenses`: Add a user-facing prose contract for actionable finding titles and direct causal bodies.
- `cli-and-local`: Add a direct, task-oriented response contract for `/ask` and finding-thread replies.

## Impact

- Review prompt composition in `src/lgtmaybe/engine/prompt.py`.
- Conversational response prompts in `src/lgtmaybe/cli/slash.py`.
- Focused prompt-contract tests in `tests/engine/test_prompt.py` and `tests/cli/test_slash.py`.
- No API, model, configuration, dependency, or rendered-output schema changes.
