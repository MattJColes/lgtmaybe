## MODIFIED Requirements

### Requirement: Built-in lenses and preset fan-out

`ReviewCategory` SHALL enumerate the built-in lenses: nine everyday categories
(security, correctness, deprecation, tests, documentation, performance,
complexity, intent, ponytail) plus `spec`. `ReviewPreset` SHALL
shape their fan-out: `fast` covers the everyday categories in four calls on
every provider; `full` runs one call per selected built-in category.
<!-- anchor: core.lenses -->

#### Scenario: fast default
- **WHEN** a review runs the `fast` preset with the default categories
- **THEN** it makes four calls (security, correctness, code health, artefacts)
  whatever the worker count

#### Scenario: full preset
- **WHEN** a review runs with `preset: full`
- **THEN** each selected built-in category runs as its own call

#### Scenario: config still lists the removed architecture lens
- **WHEN** configuration lists `architecture` under `categories`
- **THEN** the config loads, the entry is ignored, and a warning names it
