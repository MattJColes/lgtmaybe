## ADDED Requirements

### Requirement: Profile traces finding flow
<!-- anchor: engine.profile-findings -->

The opt-in review profile SHALL report the parsed finding count for every successful review-lens call and SHALL compare their total with the count returned after the pipeline. A valid empty findings payload MUST render as zero, a parse failure MUST render as an error rather than zero, and calls that do not produce review findings MUST remain uncounted.

#### Scenario: model returns valid empty findings
- **WHEN** every review lens returns a valid empty findings payload
- **THEN** each review call reports zero parsed findings and the profile reports zero parsed and zero returned

#### Scenario: response cannot be parsed
- **WHEN** a provider call succeeds but its response is not valid findings JSON
- **THEN** that call's profile row reports the parse failure and does not report zero parsed findings

#### Scenario: downstream stages remove findings
- **WHEN** review calls parse one or more findings that dedupe, suppression, reflection, or filtering later removes
- **THEN** the profile's parsed total exceeds its returned total so the loss is distinguishable from an empty model response
