## MODIFIED Requirements

### Requirement: Split-prefix prompt shape for caching
<!-- anchor: prompt.shared-preamble -->

Every review call SHALL share a lens-independent system preamble and diff prefix, with the lens checklist as the final uncached block, so routes with cache breakpoints let lenses 2..N read the preamble-plus-diff from cache. Providers without explicit cache support SHALL receive the user blocks joined into one plain user message.

#### Scenario: provider without cache support
- **WHEN** the model's route has no explicit cache breakpoint
- **THEN** the adapter joins the split user blocks into one plain user message

#### Scenario: output language configured
- **WHEN** `ReviewConfig.language` is set
- **THEN** the shared preamble carries a directive to write the `title`/`body` prose in that language while leaving structural fields and suggestion code unchanged

#### Scenario: output language unset
- **WHEN** `ReviewConfig.language` is unset
- **THEN** the shared preamble is byte-identical to the pre-language prompt
