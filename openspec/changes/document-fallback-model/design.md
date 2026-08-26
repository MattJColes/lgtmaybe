## Context

The detailed behavior already exists in the provider and review-pipeline
specifications. User documentation exposes only isolated summaries, so readers
must reconstruct the recovery order from several pages.

## Goals / Non-Goals

**Goals:**

- Give one copy-paste configuration for a primary and fallback model.
- Explain the two recovery paths without exposing implementation details.
- State that both models use the configured provider, endpoint, and credentials.
- Show where a fallback is reported and how its cost is counted.

**Non-Goals:**

- Change retry, timeout, truncation, or budget behavior.
- Add cross-provider failover or a list of fallback models.
- Recommend one model pair for every provider.

## Decisions

1. Put the detailed explanation beside `model` in the existing configuration
   guide. This is where readers choose both values and avoids a separate page
   for one setting.
2. Describe ordinary provider failures and truncation separately because their
   recovery order differs.
3. Keep the cost guide short and link it to the configuration guide rather than
   duplicate the recovery rules.

## Risks / Trade-offs

- Model names age quickly, so the example demonstrates the shape and links to
  the model-selection guide rather than claiming a permanent best pair.
- Retry details can drift, so the guide describes ordering and bounds without
  copying internal constants that belong in the architecture reference.
