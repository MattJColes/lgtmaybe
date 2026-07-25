## 1. Lock the readable-diagram contract

- [x] 1.1 Add focused engine tests requiring an automatic `flowchart LR`
  prompt, compact node and edge rules, the branched worked example, and
  ASCII fallback for legacy C4 output.
- [x] 1.2 Run the focused diagram tests red and confirm they fail on the current
  C4 prompt and prefix acceptance.

## 2. Generate automatic flowcharts

- [x] 2.1 Replace the C4 prompt and example with the compact branched flowchart
  contract, without adding a renderer, post-processing, or a dependency.
- [x] 2.2 Restrict the Mermaid prefix check to automatic flowchart syntax so
  legacy C4 output uses the existing ASCII fallback.
- [x] 2.3 Run the focused diagram tests green and refactor only if the passing
  implementation can be made smaller or clearer.

## 3. Specs and verification

- [x] 3.1 Update the anchored `cli-and-local` living requirement from C4 to the
  compact automatically laid-out Mermaid behavior; keep its anchor healthy.
- [x] 3.2 Update user-facing diagram documentation and code docstrings from
  Mermaid C4 syntax to the compact automatic flowchart contract.
- [x] 3.3 Run the diagram engine tests and CLI diagram integration tests.
- [x] 3.4 Run the full pytest suite, ruff check and format check, mypy, living
  spec tests, OpenSpec validation, and the spec drift check.
