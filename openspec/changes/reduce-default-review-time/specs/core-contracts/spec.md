## MODIFIED Requirements

### Requirement: Nine built-in lenses, preset-shaped fan-out
<!-- anchor: core.lenses -->

`ReviewCategory` SHALL enumerate the nine built-in lenses (security,
correctness, deprecation, tests, documentation, performance, complexity,
intent, ponytail). `ReviewPreset` SHALL shape the fan-out: `fast` SHALL cover
seven code-focused lenses in three calls, while `full` SHALL run all nine.

#### Scenario: default preset batches the lenses
- **WHEN** a review runs with no preset override
- **THEN** seven lenses fan out as three concurrent calls

#### Scenario: full preset restores artefact checks
- **WHEN** a review runs with `preset: full`
- **THEN** tests and documentation run alongside every other built-in lens
