## 1. Acceptance Tests

- [x] 1.1 Add failing contract and prompt tests for nullable `failure_scenario`, concrete defect examples, and `null` gap examples.
- [x] 1.2 Add failing engine tests proving low-severity defect findings without a scenario are dropped while gap and custom-lens findings remain eligible.
- [x] 1.3 Add failing reflection tests proving claimed scenarios reach the auditor and unsupported scenarios are rejected through its existing verdict.

## 2. Default-On Gate

- [x] 2.1 Extend `ReviewFinding` with backwards-compatible nullable `failure_scenario` and refresh schema snapshots.
- [x] 2.2 Update the shared output contract, built-in lens instructions, and worked examples to produce concise scenarios only for defect categories.
- [x] 2.3 Add the category-based non-blank scenario gate before reflection, independent of severity, with focused logging for dropped findings.
- [x] 2.4 Tighten the reflection prompt to disprove each claimed scenario against the diff and grounded file context.

## 3. Documentation and Verification

- [x] 3.1 Regenerate the model/config reference and update maintained structured-output documentation.
- [x] 3.2 Run the focused model, prompt, engine, reflection, CLI render, and provider contract tests.
- [x] 3.3 Run the full test, lint, type, OpenSpec, anchor, drift, and documentation gates.
