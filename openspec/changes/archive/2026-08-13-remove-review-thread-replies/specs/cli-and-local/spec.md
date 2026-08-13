## REMOVED Requirements

### Requirement: Finding-thread replies are answered in-thread
<!-- anchor: cli.review-reply -->
**Reason**: A human comment is not evidence that a finding is fixed. Automatically invoking the model duplicates commit-scoped re-review, adds cost and noise, and can respond before the changed code is evaluated.

**Migration**: Remove `answer_replies` from configuration and remove the `pull_request_review_comment` trigger from custom workflows. Use `/ask` for deliberate questions; push a commit to trigger incremental verification and resolve-on-fix.

## ADDED Requirements

### Requirement: Stale review-comment events are inert
<!-- anchor: cli.review-comment-noop -->
The Action SHALL exit successfully on a `pull_request_review_comment` event before loading review configuration, constructing a provider, or accessing GitHub, so a workflow that has not yet removed the obsolete trigger cannot spend tokens or post output.

#### Scenario: an old workflow delivers a review-comment event
- **WHEN** the Action receives `pull_request_review_comment`
- **THEN** it exits successfully without reading review config, calling a provider, or posting to GitHub

## MODIFIED Requirements

### Requirement: Conversational answers are directly actionable
<!-- anchor: cli.response-style -->
Provider prompts for `/ask` answers SHALL require the response to begin with the
direct answer, omit preamble, tangents, recap, and closing pleasantries, use
numbered steps only when the work is genuinely multi-step, and end with one
concrete next action only when action remains. Purely informational answers
SHALL stop after answering instead of inventing a task for the reader.

#### Scenario: User asks a direct question
- **WHEN** `/ask` requests information that needs no follow-up work
- **THEN** the answer leads with the result and stops without a fabricated next action

#### Scenario: Answer requires several actions
- **WHEN** an answer requires more than one bounded action
- **THEN** those actions are presented as the fewest numbered steps that still work
