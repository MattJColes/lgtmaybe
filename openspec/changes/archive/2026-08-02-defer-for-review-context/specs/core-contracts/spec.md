## MODIFIED Requirements

### Requirement: Findings are structured output only

`ReviewFinding` SHALL be the only shape a finding takes: severity, file, line,
side, title, body, optional suggestion, nullable `failure_scenario`, verbatim
`anchor` line, `anchored` flag, 0-10 `confidence`, and the originating lens
`category`. Models are strict (`extra="forbid"`), so drifted or injected fields
are rejected — prose is never parsed. The `ReviewResult` envelope carrying them
MAY also carry `needs`: the paths or symbols the lens must read before it can
decide, defaulting to empty so a response without one still validates.
<!-- anchor: core.finding -->

#### Scenario: model returns an unexpected field
- **WHEN** the LLM's JSON carries a field the contract doesn't declare
- **THEN** validation rejects it rather than silently accepting it

#### Scenario: legacy code constructs a finding
- **WHEN** a caller omits `failure_scenario`
- **THEN** the field defaults to `null` so compatibility is preserved until the
  engine applies category-specific eligibility

#### Scenario: the envelope carries a deferral
- **WHEN** the response is `{"findings": [...], "needs": ["app/models.py"]}`
- **THEN** the findings parse as usual and the deferral is read separately, so
  a lens reports what it knows and asks for what it needs in one answer
