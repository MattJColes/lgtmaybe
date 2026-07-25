## ADDED Requirements

### Requirement: Defect prompts require a concrete failure scenario

Every built-in review prompt SHALL request a nullable `failure_scenario`.
Security, correctness, deprecation, and performance findings SHALL describe a
concrete trigger, the changed behaviour, and its observable impact regardless
of severity; tests, documentation, complexity, intent, and ponytail findings
SHALL return `null` rather than invent a causal story.
<!-- anchor: prompt.failure-scenario -->

#### Scenario: correctness lens finds a low-severity defect
- **WHEN** the correctness lens reports a defect as `low`
- **THEN** it still returns a concrete `failure_scenario`

#### Scenario: tests lens reports missing coverage
- **WHEN** the tests lens reports a real coverage gap
- **THEN** it returns `failure_scenario: null`
