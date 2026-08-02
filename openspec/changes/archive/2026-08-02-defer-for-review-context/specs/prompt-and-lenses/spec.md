## MODIFIED Requirements

### Requirement: Split-prefix prompt shape for caching

With `prompt_cache` on (default), every review call SHALL share a
lens-independent system preamble and diff prefix, with the lens checklist as
the final uncached block — so on routes with cache breakpoints, lenses 2..N
read the preamble-plus-diff from cache. Other providers get the blocks joined
back into the single plain message they always received. Anything a single lens
adds mid-review — the files it deferred for — SHALL ride its own uncached block,
never the shared prefix.
<!-- anchor: prompt.shared-preamble -->

#### Scenario: provider without cache support
- **WHEN** the model's route has no explicit cache breakpoint
- **THEN** the call is byte-for-byte the legacy single-message shape

#### Scenario: output language configured
- **WHEN** `ReviewConfig.language` is set
- **THEN** the shared preamble carries a directive to write the `title`/`body`
  prose in that language (structural fields and `suggestion` code unchanged),
  keyed on the language so the prefix stays byte-identical across the fan-out
- **WHEN** `language` is unset
- **THEN** the preamble is byte-identical to the pre-language prompt

#### Scenario: mid-review retrieval configured
- **WHEN** `mid_review_retrieval` is on
- **THEN** the preamble asks for a one-round `needs` deferral, and a lens re-run
  with fetched files carries them in its own block so its siblings still hit cache
- **WHEN** it is off (the default)
- **THEN** the preamble and every legacy system prompt are byte-identical to a
  build without the feature
