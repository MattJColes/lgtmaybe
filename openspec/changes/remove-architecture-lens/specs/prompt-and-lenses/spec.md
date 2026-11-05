## ADDED Requirements

### Requirement: An empty review is the expected outcome

The shared review rules SHALL state that an empty result is the expected answer
when a change has nothing worth raising, and SHALL hold every lens to one bar: a
finding is something a competent engineer would change before merging. No lens
SHALL raise a change that only swaps one acceptable form for another. The rule
SHALL NOT weaken a security or correctness finding that shows a concrete defect.
<!-- anchor: prompt.worth-raising -->

#### Scenario: a correct change with a matter-of-taste alternative
- **WHEN** a change is correct and the only available comment is a preference
- **THEN** the lens returns `{"findings": []}`

#### Scenario: a small change with a real bug
- **WHEN** a one-line change introduces a concrete defect
- **THEN** the bar does not stop the lens reporting it

## MODIFIED Requirements

### Requirement: Fast preset is four distinct lenses on every provider

The default `fast` preset SHALL run every everyday category as FOUR lenses —
security, correctness, code health, and artefacts — one per concern. The lens set SHALL NOT vary with the number of available workers: a
single-worker configuration runs the same four calls serially. Stated intent
SHALL fold into the correctness call rather than consume its own.
<!-- anchor: prompt.groups -->

#### Scenario: cloud default
- **WHEN** `fast` uses a cloud provider with auto-concurrency
- **THEN** its four calls are security, correctness, code health, and artefacts,
  and they may overlap

#### Scenario: local single-slot default
- **WHEN** `fast` uses Ollama with auto-concurrency
- **THEN** the same four calls run within the single-worker pool, serially

#### Scenario: intent with no dedicated call
- **WHEN** the fast preset runs and the PR states an intent
- **THEN** intent findings come out of the correctness call, attributed to the
  intent category

#### Scenario: everyday review covers artefacts
- **WHEN** the fast preset runs with no category override
- **THEN** tests and documentation are reviewed by the artefacts call

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

## REMOVED Requirements

### Requirement: Architecture findings name a change scenario

**Reason**: The built-in architecture lens is removed; it produced speculative design findings on every review.
**Migration**: Use the opt-in `pack:design` lens, or a custom lens in `extra_lenses`.

## RENAMED Requirements

- FROM: `### Requirement: Fast preset is five distinct lenses on every provider`
- TO: `### Requirement: Fast preset is four distinct lenses on every provider`
