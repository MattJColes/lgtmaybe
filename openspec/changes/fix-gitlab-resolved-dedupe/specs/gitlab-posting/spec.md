## MODIFIED Requirements

### Requirement: The summary lives in an editable note
<!-- anchor: gitlab.upsert -->

The summary SHALL be posted as a merge request note carrying the hidden marker,
upserted in place on a re-run, with disjoint marker families for the summary,
the description, and the diagram. Findings whose hidden ids are present in
unresolved discussions SHALL NOT be posted again. Resolved discussions SHALL
not suppress a finding that later reappears.

#### Scenario: a re-run edits rather than duplicates
- **WHEN** a review runs a second time on the same merge request
- **THEN** the existing summary note is edited and no second one is posted

#### Scenario: a resolved finding returns
- **WHEN** a resolved discussion's finding is produced by a later review
- **THEN** a new discussion is created for the regression
