## 1. Regression Tests

- [x] 1.1 Add a failing diagram test that replays the reported parenthesized label and requires locally generated, quoted Mermaid.
- [x] 1.2 Add a failing `/ask` test that rejects review-shaped JSON and verifies the task-specific response schema.

## 2. Structured Output Implementation

- [x] 2.1 Replace raw diagram presentation fields with typed graph nodes and edges while retaining the legacy ASCII-only fallback.
- [x] 2.2 Render Mermaid and ASCII deterministically with stable ids, escaped labels, a six-node cap, and invalid-edge filtering.
- [x] 2.3 Parse `/ask` through a typed answer result and return a safe message for wrong-schema JSON.

## 3. Specification and Verification

- [x] 3.1 Update the anchored `cli-and-local` requirement to describe validated answer and graph output behavior.
- [x] 3.2 Run focused diagram/slash tests, relevant integration tests, linters, model contracts, and spec validation.
