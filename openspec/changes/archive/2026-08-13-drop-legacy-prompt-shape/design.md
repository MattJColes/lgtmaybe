## Context

Review calls currently build both a legacy lens-in-system layout and the default split-prefix layout. The provider adapter already merges split user blocks for routes without explicit cache support.

## Goals / Non-Goals

**Goals:** Keep one prompt assembly path, preserve prompt content and supported-route cache behavior, and remove the public opt-out.

**Non-Goals:** Change cache eligibility, breakpoints, warm-up thresholds, token accounting, or lens content.

## Decisions

Always construct the split shape in the engine and reflection pass. Remove the provider's boolean switch while retaining automatic route capability detection: cacheable routes receive breakpoints and a stable key; other routes receive merged plain user content. Repoint coverage tests at the shared preamble plus focused block instead of retaining compatibility wrappers.

## Risks / Trade-offs

- Removing a public field can reject old configuration files → document the migration as deleting `prompt_cache`; the previous default already selected the retained path.
- Prompt content could drift while deleting duplicate builders → update tests first to assert the retained builders and run the complete prompt/provider suites.
- Anchor rules point at deleted functions → re-point each rule at its retained counterpart in the same change.
