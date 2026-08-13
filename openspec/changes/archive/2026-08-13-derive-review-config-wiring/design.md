## Context

Click already delivers option values by parameter name, and Pydantic owns the configuration field set.

## Goals / Non-Goals

**Goals:** Remove name-only duplication while preserving explicit runtime-only and intentionally CLI-only distinctions.

**Non-Goals:** Generate `action.yml` or change the exposed option set.

## Decisions

Use `**inputs` in `review`, pop non-config local/runtime values, then pass the remaining mapping to `_load_cfg`. Build `_ACTION_INPUTS` from model fields minus an explicit exclusion set plus runtime inputs; the existing action.yml parity test fails when a newly derived input is not declared and mapped.

## Risks / Trade-offs

- A Click option could accidentally reach Pydantic → `_load_cfg` rejects unknown names and focused tests cover every local option.
