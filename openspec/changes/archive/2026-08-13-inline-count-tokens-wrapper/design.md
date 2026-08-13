## Context

`LiteLLMProvider._with_cache_control` is the only caller of `_count_tokens`. The wrapper exists solely to keep the engine import lazy.

## Goals / Non-Goals

**Goals:**

- Preserve the lazy import and token-counting behaviour.
- Remove the single-use delegating symbol.

**Non-Goals:**

- Change cache thresholds, message shaping, or token counting.

## Decisions

Import `count_tokens` at the start of `_with_cache_control`, after the early returns that do not need token counting. This keeps engine loading lazy and avoids importing it for non-cacheable routes. Direct module-level import was rejected because it would change import-time coupling.

## Risks / Trade-offs

- The function gains a local import → existing Python import caching makes repeated calls negligible, and the current wrapper already performs the same local import per call.
