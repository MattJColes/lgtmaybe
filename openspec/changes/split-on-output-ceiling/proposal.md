## Why

The dogfood review of PR #309 posted: "⚠️ 3 of 4 review calls failed
(ProviderTruncated: response truncated at the model's output limit after 16384
tokens)". Three of four lenses produced nothing at all. PR #311 lost one of four
on a fifteen-line diff.

The engine already knows how to recover from a payload one call cannot cover:
`_review_split` halves the batch and reviews the pieces. But it was reachable
only from a wall timeout, so a blown output ceiling — which says the same thing
about the payload — discarded the entire lens instead. The error message even
told a human to apply the fix by hand ("lower `max_input_tokens` so each call
has less to say about"); nothing applied it automatically.

Two details the observed runs corrected:

- The 16,384 ceiling was not the model's. It was lgtmaybe's own configured
  `max_tokens` against a model good for 65,536, so "the model's output limit"
  pointed the reader at a knob they cannot move.
- Truncation is not primarily input-size-driven. A reasoning model spends the
  same `max_tokens` budget on thought, which is how a fifteen-line diff
  truncates before it emits much JSON at all.

## What Changes

- Fire the batch-splitting retry for `ProviderTruncated` as well as
  `ProviderWallTimeout`, and rename the callback to the payload-shaped
  `on_oversized` so its name says what it actually handles.
- Carry the truncated response body on `ProviderTruncated` and salvage the
  findings completed before the cut, matching what the parse path already does
  for a truncation it detects itself. The lens still counts as failed, so the
  incomplete-results notice keeps firing.
- Reword the ceiling error to name `max_tokens` (not "the model's output
  limit") and to report reasoning tokens where the route exposes them.
- Keep the split bounded to one level: a piece reviewed with `batch=None` that
  truncates again reports the reason — naming `max_tokens`, the lever that is
  left once shrinking is spent — and never recurses.
- Leave `_is_permanent` treating `ProviderTruncated` as permanent: the engine
  splits, the provider must not re-send the same oversized call.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `review-pipeline`: The split/retry trigger covers both per-request budgets,
  and salvages a truncated lens's completed findings.
- `provider-gateway`: The truncation failure names `max_tokens` and reasoning
  tokens, and carries the cut-off body for the engine to salvage.

## Impact

A review whose calls run past the output ceiling now returns findings instead of
an empty lens, with the shrink and the failure both disclosed. No new
configuration, no new dependency, and no change to how many calls a healthy
review makes. Splitting is not guaranteed to fix a reasoning-bound truncation —
a piece can exhaust the cap however small it is — so that terminal state stays
reachable and now names `max_tokens` rather than only the diff size.
