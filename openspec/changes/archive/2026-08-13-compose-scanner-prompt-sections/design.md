## Context

Three narrowed prompt constants are derived by removing exact text from assembled sections.

## Goals / Non-Goals

**Goals:** Make carve-outs explicit inputs to section construction.

**Non-Goals:** Change prompt coverage, scanner selection, or finding severity.

## Decisions

Use one small builder per affected section, selecting optional grading text and bullets before interpolation. This keeps each full/narrow pair adjacent and makes rewording a fragment affect both variants deliberately.

## Risks / Trade-offs

- Whitespace can change during composition → existing byte/content-level prompt tests cover the generated sections.
