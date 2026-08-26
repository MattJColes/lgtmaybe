## MODIFIED Requirements

### Requirement: Each finding is its own positioned discussion
<!-- anchor: gitlab.positions -->

GitLab has no batched review object, so every inline finding SHALL be posted as
its own discussion carrying a `position` object that names the old and new path,
the relevant old or new line translated from lgtmaybe's RIGHT/LEFT side
vocabulary, and the three cached diff refs. A rejected position SHALL be logged
and the finding SHALL be demoted into the editable summary rather than lost. A
finding SHALL also be demoted when it is not confidently anchored, when it does
not land on a real commentable diff line, or when the diff refs are unknown.

#### Scenario: one unplaceable finding does not lose the others
- **WHEN** GitLab rejects a discussion's position
- **THEN** that finding appears in the summary and the remaining findings and
  summary still post
