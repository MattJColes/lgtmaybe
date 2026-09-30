## MODIFIED Requirements

### Requirement: Labels touch only our own families
<!-- anchor: github.labels -->

Labels SHALL derive from data the review already computed — with `pr_labels`
on: `review-effort/1-5`, `possible-security-issue`, `consider-splitting`, and,
when risk assessment is enabled, `risk/<level>` — and reconciliation SHALL touch
only lgtmaybe's own label families — best-effort, never failing the review. The
`risk/` family carries exactly one label, so a change of level replaces the
previous one.

#### Scenario: repo has unrelated labels
- **WHEN** labels are reconciled
- **THEN** labels outside lgtmaybe's families are never added or removed

#### Scenario: risk level changes between pushes
- **WHEN** a PR labelled `risk/medium` is re-reviewed and assessed `high`
- **THEN** `risk/medium` is removed and `risk/high` is added

#### Scenario: risk assessment disabled
- **WHEN** `pr_labels` is on and `risk.enabled` is false
- **THEN** no `risk/` label is added and none is removed
