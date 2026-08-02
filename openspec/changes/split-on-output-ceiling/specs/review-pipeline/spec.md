## MODIFIED Requirements

### Requirement: An oversized batch is retried smaller, never repeated
<!-- anchor: engine.timeout-split -->

A lens call that exhausts a per-request budget SHALL be retried on smaller
pieces of the same batch rather than re-sent unchanged, bounded to one split
level, with the shrink disclosed in the summary. Both budgets trigger it: the
wall clock, and the `max_tokens` ceiling an answer runs into. Findings the model
completed before a truncation SHALL be kept, and the lens SHALL still count as
failed.

#### Scenario: a multi-file batch times out
- **WHEN** a lens call on a batch of several files exceeds its wall-clock budget
- **THEN** the batch is halved by file and each half reviewed in its own call, and
  the summary reports that a batch was shrunk

#### Scenario: a single-file batch times out
- **WHEN** the timed-out batch holds one file
- **THEN** its hunks are divided into two groups, one review call each, so an
  oversized lone file still shrinks

#### Scenario: a call runs past its output ceiling
- **WHEN** a lens call's answer stops at the `max_tokens` ceiling
- **THEN** the batch is split the same way, and the findings finished before the
  cut are kept alongside the pieces' findings

#### Scenario: a piece exhausts its budget as well
- **WHEN** a piece of an already-split batch times out or truncates again
- **THEN** it fails as an ordinary failed call naming `max_tokens` — no recursion

#### Scenario: one piece answers and another fails
- **WHEN** part of a split batch is reviewed and part fails
- **THEN** the findings are kept AND the failure is reported, so the summary never
  claims a shrunk batch was reviewed when some of it was not

#### Scenario: the failure says nothing about size
- **WHEN** a lens call fails for any other reason (quota, bad key, unparseable)
- **THEN** no split happens, because nothing suggests the payload was the problem
