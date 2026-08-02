## ADDED Requirements

### Requirement: Review instructions and context scope to a directory

Extra instructions and reference files SHALL be scopeable to part of the repo
via `ReviewConfig.directory_rules`. A rule's path globs select the files it
applies to (an empty list applies it everywhere) using the same matcher as the
path filters; its instructions and the text of its `context_files` are handed to
every lens reviewing a batch that touches a matched file, and to no other batch.
Context text SHALL be read from the checked-out workspace — trusted base content
on `pull_request_target`, never the PR head — redacted and bounded by the
retrieval budget and file cap, with unreadable paths skipped. The rendered block
SHALL join the per-batch cacheable prefix, leaving the cross-batch system
preamble byte-identical to a review with no rules configured.
<!-- anchor: prompt.directory-rules -->

#### Scenario: a rule matches one batch
- **WHEN** a rule scoped to `payments/**` is configured and a review batches
  `payments/` and `docs/` files separately
- **THEN** only the `payments/` batch's calls carry the rule's instructions and
  context files

#### Scenario: context file names an unreadable path
- **WHEN** a `context_files` entry is missing, or resolves outside the workspace
  root
- **THEN** it is skipped and the review proceeds

#### Scenario: no rules are configured
- **WHEN** `directory_rules` is empty
- **THEN** no workspace file is read and the prompt is byte-identical to before
  the feature
