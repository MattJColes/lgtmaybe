# prompt-and-lenses Specification

## Purpose

How the review prompt is composed (`engine/prompt.py`): a lens-independent
cacheable preamble, per-lens checklists each with a worked example, the fast
preset's four-call grouping, and user-supplied custom lenses that fan out
identically to the built-ins.
## Requirements
### Requirement: Split-prefix prompt shape for caching

With `prompt_cache` on (default), every review call SHALL share a
lens-independent system preamble and diff prefix, with the lens checklist as
the final uncached block — so on routes with cache breakpoints, lenses 2..N
read the preamble-plus-diff from cache. Other providers get the blocks joined
back into the single plain message they always received.
<!-- anchor: prompt.shared-preamble -->

#### Scenario: provider without cache support
- **WHEN** the model's route has no explicit cache breakpoint
- **THEN** the call is byte-for-byte the legacy single-message shape

#### Scenario: output language configured
- **WHEN** `ReviewConfig.language` is set
- **THEN** the shared preamble carries a directive to write the `title`/`body`
  prose in that language (structural fields and `suggestion` code unchanged),
  keyed on the language so the prefix stays byte-identical across the fan-out
- **WHEN** `language` is unset
- **THEN** the preamble is byte-identical to the pre-language prompt

### Requirement: Every focused prompt teaches by worked example

Each lens prompt SHALL carry exactly one category-matched worked example with
a real hunk header, and the contract SHALL explain the `line`/`side`
arithmetic — the model is taught the coordinate system, not assumed to know it.
<!-- anchor: prompt.system -->

#### Scenario: security lens prompt is built
- **WHEN** the security lens call is composed
- **THEN** its prompt contains one security worked example with a real `@@`
  hunk header

### Requirement: Custom lenses are trusted config, fanned out uniformly

Users SHALL add lenses via `extra_lenses` (id + instructions, optional worked
example); the engine builds a uniform lens per built-in category and per
custom lens and fans them all through the same merge/dedupe/reflect pipeline.
Lens text enters the system prompt, so it is trusted config only — never
sourced from PR-author content.
<!-- anchor: prompt.custom-lens -->

#### Scenario: a custom lens joins a review
- **WHEN** `.lgtmaybe.yml` defines an extra lens
- **THEN** it runs as one more concurrent lens call, findings merged like any
  built-in

### Requirement: Fast preset is four distinct lenses on every provider

The default `fast` preset SHALL run every built-in category as FOUR lenses —
security, correctness, code health, and artefacts — one per concern. The lens
set SHALL NOT vary with the number of available workers: a single-worker
configuration runs the same four calls serially. Stated intent SHALL fold into
the correctness call rather than consume its own.
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
