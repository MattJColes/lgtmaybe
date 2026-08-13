## 1. Regression tests

- [x] 1.1 Change the Action and gate tests to require automatic diagrams on `synchronize`, then run them red against the current open/reopen-only gate.
- [x] 1.2 Add a renderer test requiring a provider-supplied PR summary above the diagrams, then run it red against the current diagram schema/output.
- [x] 1.3 Add a prompt test requiring an impact-first summary with one change per sentence and no preamble or tangents.

## 2. Minimal implementation

- [x] 2.1 Give automatic diagrams a synchronize-aware event gate without changing automatic description eligibility.
- [x] 2.2 Add the optional summary to the existing diagram structured response, prompt, translation fields, and comment renderer.
- [x] 2.3 Shape the summary prompt with the concise-output principles from the referenced `i-have-adhd` skill without adding a runtime dependency.

## 3. Documentation and verification

- [x] 3.1 Update Action and user-guide text to describe diagram refreshes on pushes and the change summary in the same comment.
- [x] 3.2 Run focused CLI/diagram tests, the relevant CLI integration suite, format/lint/type checks, and the living-spec validation suite.
