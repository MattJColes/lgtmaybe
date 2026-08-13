## 1. Acceptance Tests

- [x] 1.1 Add prompt-contract tests proving review prompts require action-first titles, direct causal bodies, and plain problem titles when no concrete fix exists; run them and confirm they fail.
- [x] 1.2 Add slash-command tests proving `/ask` and finding-thread reply prompts require answer-first prose, bounded numbered steps, no conversational padding, and only a conditional final action; run them and confirm they fail.

## 2. Prompt Updates

- [x] 2.1 Update the shared review prompt with the minimal finding-prose contract while preserving the structured schema, literal-code suggestion rules, localization, and cache shape.
- [x] 2.2 Update the `/ask` and finding-thread reply prompts with their tailored conversational response contract while preserving untrusted-input instructions and structured `/ask` output.
- [x] 2.3 Run the focused acceptance tests and refactor duplicated or conflicting wording only if the tests expose it.

## 3. Verification

- [x] 3.1 Run the prompt, slash-command, and CLI integration tests to confirm the existing parse and posting flows remain compatible.
- [x] 3.2 Run formatter, linter, and `tests/specs` checks; confirm the existing living-spec anchors remain healthy and note the new delta anchors for sync/archive.
- [x] 3.3 Validate the OpenSpec change strictly and confirm every task and scenario remains aligned with the approved scope.
