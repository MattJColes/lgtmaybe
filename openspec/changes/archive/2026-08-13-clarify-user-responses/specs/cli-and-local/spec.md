## ADDED Requirements

### Requirement: Conversational answers are directly actionable
<!-- anchor: cli.response-style -->

Provider prompts for `/ask` answers and finding-thread replies SHALL require the
response to begin with the direct answer, omit preamble, tangents, recap, and
closing pleasantries, use numbered steps only when the work is genuinely
multi-step, and end with one concrete next action only when action remains.
Purely informational answers SHALL stop after answering instead of inventing a
task for the reader.

#### Scenario: User asks a direct question
- **WHEN** `/ask` requests information that needs no follow-up work
- **THEN** the answer leads with the result and stops without a fabricated next action

#### Scenario: Answer requires several actions
- **WHEN** an answer requires more than one bounded action
- **THEN** those actions are presented as the fewest numbered steps that still work

#### Scenario: Finding thread has one remaining action
- **WHEN** a finding-thread reply confirms that the finding still needs work
- **THEN** the reply ends with exactly one concrete next action for the author
