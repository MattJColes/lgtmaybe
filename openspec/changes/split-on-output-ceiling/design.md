## Context

`EngineImpl._review_split` halves an oversized batch and reviews the pieces —
the one retry a payload-shaped failure can benefit from, since re-sending the
identical request cannot succeed. `_complete_lens` reached it through a callback
named `on_wall_timeout`, guarded by `isinstance(exc, ProviderWallTimeout)`.

`ProviderTruncated` (raised in `LiteLLMProvider._map_response` when
`finish_reason == "length"`) fell past that guard to `return [], reason`, losing
the whole lens. The inconsistency was visible inside one function: the *parse*
path for a truncated body already salvaged the findings completed before the cut
(`ParseError.truncated` / `.recovered`); the *exception* path discarded
everything.

## Goals / Non-Goals

**Goals:**

- Recover findings from a review call that runs past its output ceiling.
- Keep the salvage semantics identical on both truncation paths.
- Point a reader at the knob that will actually move.
- Keep the retry bounded and the failure visible.

**Non-Goals:**

- Adapter-level retry of a truncated call (it can only re-send the same
  oversized request).
- Any new configuration surface, dependency, or cost model.
- Guaranteeing the split fixes every truncation.

## Decisions

- **One trigger set, one name.** The callback becomes `on_oversized` and fires
  for `ProviderWallTimeout | ProviderTruncated`. Both mean "one call was asked to
  cover more than it could finish"; the remedy is the same, so the name is about
  the payload rather than about one of its symptoms.
- **The body rides the exception.** `ProviderTruncated` gains a `text` field
  carrying the cut-off response. The engine feeds it through the existing
  `parse_findings` / `ParseError.recovered` machinery, so a salvage on the
  exception path and a salvage on the parse path produce the same result from
  the same code. Riding the exception (rather than being returned) preserves the
  property that a caller cannot take the salvage without also seeing the failure.
- **Salvage plus split, not either/or.** Salvaged findings are prepended to the
  pieces' findings. They cover the same code, so the existing location-keyed
  dedupe collapses any overlap; what survives is anything the pieces missed.
- **Bounded to one level, unchanged.** Pieces are still reviewed with
  `batch=None`, which leaves `on_oversized` as None — the terminal case returns
  the salvage and the reason rather than splitting again. This is a reachable
  state, not a theoretical one (below), so it is covered by its own test.
- **The ceiling is `max_tokens`, not the model.** In the run that prompted this
  it was lgtmaybe's own configured 16,384 against a model good for 65,536.
  Naming "the model's output limit" sent the reader to a limit they cannot move.
- **Name the reasoning spend.** litellm normalises
  `completion_tokens_details.reasoning_tokens` on the routes that report it; it
  is read defensively and named in the message when present. It is the whole
  explanation for a fifteen-line diff truncating, and it is the difference
  between "my diff is too big" (wrong) and "my cap is too low for this model"
  (right). Absent detail simply omits the breakdown — no dependency, no fallback
  estimate.
- **`_is_permanent` keeps `ProviderTruncated` permanent.** Not in tension with
  the engine's new retry: only the engine holds the batch, so only the engine
  can change the payload. The adapter can only re-send the same oversized one,
  which is the attempt worth refusing — at a full ceiling-length generation each.

## Risks / Trade-offs

- **The split may not fix a reasoning-bound truncation.** Thinking tokens come
  out of the same `max_tokens` budget, so a piece can exhaust the cap however
  small it is. Accepted: a smaller batch is less to think about as well as less
  to write about, so it often helps, and when it does not the failure terminates
  naming `max_tokens`.
- **Up to twice the calls for a truncating batch.** Same trade the wall-timeout
  split already made, and bounded by the same one split level.
- **Salvaged findings come from a lens that was cut short.** They are
  schema-valid and deduped, and the lens still counts as failed, so the summary
  never presents a partial lens as a complete one.
