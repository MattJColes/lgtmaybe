## REMOVED Requirements

### Requirement: One config surface with ordered severities
**Reason**: The requirement includes the removed `answer_replies` default and must be replaced without that obsolete scenario.

**Migration**: Use the replacement typed-config requirement and delete `answer_replies` from existing configuration.

## ADDED Requirements

### Requirement: Review configuration is typed with ordered severities
<!-- anchor: core.config -->
`ReviewConfig` SHALL be the single knob surface for a review (provider, model,
filters, caps, toggles like `learn_feedback`); `Severity` SHALL order `info <
low < medium < high < critical` so floors like `min_severity` and `fail_on`
compare with `>=`. `fail_on` is an optional `Severity` (default `None` = off)
driving the merge-gate Check Run. Removed fields such as `answer_replies` SHALL
be rejected by strict configuration validation rather than accepted as no-ops.

#### Scenario: severity floor filters findings
- **WHEN** `min_severity` is `medium`
- **THEN** `low` and `info` findings are dropped before posting

#### Scenario: merge-gate threshold is off by default
- **WHEN** a `ReviewConfig` is built without `fail_on`
- **THEN** `fail_on` is `None` and no check run is created

#### Scenario: a removed option is configured
- **WHEN** configuration contains `answer_replies`
- **THEN** validation rejects it with the same unknown-field behavior as any unsupported option

#### Scenario: an unknown reasoning effort is rejected at load
- **WHEN** `reasoning_effort` is set to a value outside the normalised set
- **THEN** config validation fails, rather than the route rejecting every lens
  call mid-review
