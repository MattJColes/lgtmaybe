## Context

All notice data already exists as local values at the end of `review`.

## Goals / Non-Goals

**Goals:** Make `review` read as pipeline plus one notice-rendering call; preserve the transparency contract.

**Non-Goals:** Extract every pipeline stage or change summary prose.

## Decisions

Use a frozen `_NoticeState` to avoid a long parameter list. `_build_notices` owns ordering and bespoke formatting, with a compact renderer table for the repeated count-only notices.

## Risks / Trade-offs

- Notice ordering or wording could drift → the existing notice suites and a direct builder test pin both.
