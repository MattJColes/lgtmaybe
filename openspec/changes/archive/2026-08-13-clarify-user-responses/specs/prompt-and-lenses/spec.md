## ADDED Requirements

### Requirement: Review findings lead with the corrective action
<!-- anchor: prompt.response-style -->

Review prompts SHALL require user-facing finding prose to make the correction
obvious: when a concrete fix is known, the title leads with that action; the
body then states the cause and observable impact directly, without preamble,
repetition, tangents, recap, or closing pleasantries. When no concrete action is
known, the title SHALL state the problem plainly instead of inventing one.

#### Scenario: Finding has a concrete correction
- **WHEN** a review lens reports a finding with a concrete fix
- **THEN** its title leads with the corrective action and its body explains the cause and observable impact

#### Scenario: Finding requires a judgement call
- **WHEN** a review lens reports a valid finding without a concrete drop-in fix
- **THEN** its title states the problem directly and its body carries the recommendation without inventing replacement code
