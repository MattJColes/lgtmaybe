## MODIFIED Requirements

### Requirement: A blown output ceiling is named, not retried
<!-- anchor: provider.truncation -->

A completion that stopped because it ran out of output tokens SHALL be raised
as its own failure naming `max_tokens` as the ceiling reached — usually a value
the user set, not the model's own — plus the reasoning-token count where the
route reports it, and SHALL carry the cut-off body so the engine can salvage the
findings finished before the cut. It SHALL NOT be retried — at temperature 0 the
identical request reaches the identical ceiling, and each attempt costs a full
ceiling-length generation — while a configured fallback model is still tried.
Detection reads the finish reason only where the route reports it plainly:
litellm rewrites a reason it does not recognise to `stop`, so a route that
names a ceiling hit its own way is caught downstream by the parser instead.

#### Scenario: the model generates to its output limit
- **WHEN** a completion returns with a `length` finish reason
- **THEN** the call fails naming the token count reached and `max_tokens`, is not
  retried, and carries the truncated body

#### Scenario: a reasoning model spends the budget on thought
- **WHEN** the route reports reasoning tokens on a truncated completion
- **THEN** the failure names them, because that — not diff size — is why a small
  diff hit the ceiling

#### Scenario: the route misreports why it stopped
- **WHEN** a provider reports a ceiling hit under a name litellm maps to `stop`
- **THEN** the response still reaches the parser, which reports the truncation
  from the unclosed JSON itself
