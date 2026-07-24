## MODIFIED Requirements

### Requirement: Fast preset focuses seven lenses into three calls
<!-- anchor: prompt.groups -->

The default `fast` preset SHALL run dedicated security and correctness calls
(with stated intent folded into correctness) plus one merged code-health call
covering performance, complexity, ponytail, and deprecation. Tests and
documentation SHALL remain available through `full` or explicit categories.

#### Scenario: intent with no dedicated call
- **WHEN** the fast preset runs and the PR states an intent
- **THEN** intent findings come out of the correctness call, attributed to the
  intent category

#### Scenario: everyday review
- **WHEN** the fast preset runs with no category override
- **THEN** tests and documentation do not consume a model call
