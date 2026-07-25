## Why

A finding can be valid JSON, anchored to a changed line, and still be a plausible assertion with no concrete way for the change to fail. Requiring evidence based on model-selected severity leaves an escape hatch because the model can lower the severity.

## What Changes

- Add a nullable `failure_scenario` field to the structured finding contract.
- Require a concrete trigger, changed behaviour, and observable impact for defect findings from the security, correctness, deprecation, and performance categories, regardless of severity.
- Leave gap and maintainability findings from tests, documentation, complexity, intent, and ponytail categories eligible with `failure_scenario: null`.
- Make the reflection pass verify each claimed failure scenario against the changed diff and available file context; missing or unsupported scenarios are dropped before posting.
- Enable the gate by default with no configuration toggle and no A/B benchmark.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `core-contracts`: Extend the strict `ReviewFinding` wire format with the nullable failure scenario.
- `prompt-and-lenses`: Teach built-in and custom review calls how to return concrete failure scenarios without inventing them for gap findings.
- `finding-quality`: Gate defect findings on a non-empty, reflection-supported failure scenario independently of model-selected severity.

## Impact

The change affects the Pydantic finding schema and snapshots, review prompts and worked examples, finding parsing, reflection verdict handling, engine filtering, serialized CLI output, and focused engine/provider tests. It adds no dependency or user-facing configuration.
