## 1. Acceptance Coverage

- [x] 1.1 Add a regression test proving a provider call cannot exceed its
  configured wall-clock timeout when the underlying SDK hangs
- [x] 1.2 Update preset tests to require three default calls and retain all
  categories in `full`
- [x] 1.3 Keep the supplied-workflow C4 assertion green

## 2. Runtime Optimization

- [x] 2.1 Enforce the configured timeout around each raw LiteLLM completion
- [x] 2.2 Remove the tests/documentation call from the `fast` preset
- [x] 2.3 Enable the existing profile summary in the dogfood workflow

## 3. Documentation

- [x] 3.1 Update user-facing preset and timeout descriptions
- [x] 3.2 Regenerate derived configuration and LLM documentation

## 4. Verification

- [x] 4.1 Run focused timeout, preset, and workflow tests
- [x] 4.2 Run the complete unit/integration suite, lint, and type checks
- [x] 4.3 Run spec-anchor checks and validate the OpenSpec change
