## Context

The current incremental base is a reviewed-SHA embedded in the summary review. Posting and diagram generation are independent, and resolve-on-fix treats an outdated finding that the incremental review did not reproduce as fixed. The GitHub port is frozen, while the concrete adapter already exposes incremental and resolution helpers beyond that port.

## Goals / Non-Goals

**Goals:**

- Make a hidden marker durable proof that all required outputs exist for one head.
- Keep subsequent model work proportional to the new diff and number of open findings.
- Fail closed: missing context or malformed validation can never resolve a thread.

**Non-Goals:**

- Persist state outside GitHub, add configuration, change local review, or change provider authentication.
- Repost still-open findings, answer finding conversations, or make GraphQL resolution a completion gate.

## Decisions

### The diagram marker is the end-to-end commit point

When automatic diagrams are enabled, the review posts first and the diagram upsert follows with `<!-- lgtmaybe-diagrammed:<sha> -->`. A diagram marker is written only by the automatic orchestrator after a complete review; manual `/diagram` never receives a completed SHA. Its presence therefore proves both outputs existed in order. The adapter returns that SHA only when the matching review summary still exists. With diagrams disabled, the existing reviewed marker remains the completion point.

This avoids a third “commit” write after the diagram and preserves the required review-before-comment ordering. A database or check-run state would add persistence and permissions for no benefit.

### Hybrid runs combine incremental discovery with explicit validation

The existing engine reviews the compare diff, preserving new-regression coverage. The adapter also returns active lgtmaybe finding roots with their thread id, hidden identities, path, line, body, outdated state, and original diff hunk. Findings reproduced by the incremental engine remain open deterministically. Each unmatched prior finding is validated against the untrusted prior body, compare diff, and available current file text using `reflect_model` or `model` and a strict structured envelope.

Statuses are `fixed`, `still_open`, and `uncertain`. Parser failure, absent verdicts, duplicate identities, and insufficient context become `uncertain`. One validation call handles all unmatched findings so the hybrid path adds a bounded pass rather than one call per thread; the existing review input cap truncates context and forces uncertainty instead of guessing.

### Explicit fixed identities drive resolution

The orchestrator supplies validated fixed thread ids to the adapter before posting the updated review. Hybrid posting bypasses absence-based resolution and resolves only that allowlist. Full reviews retain the existing outdated-and-absent behaviour so `/review full` remains backward compatible. Resolution keeps its current order: resolve, rewrite markers, then reply.

### Incomplete and same-head runs do not advance state

The reviewed marker is not advanced when the summary carries the incomplete marker. A required diagram failure exits non-zero and leaves the prior diagrammed SHA intact. When current and completed SHA match, orchestration returns an empty result and completion message without provider or GitHub writes. Compare failures and non-ahead histories use the existing full fallback.

## Risks / Trade-offs

- **Validation can miss cross-file evidence** → include the complete compare diff and fetched head text; unresolved evidence returns `uncertain`.
- **A hybrid run adds one model call when old findings exist** → reuse the reflection model and skip the call for deterministic reproductions or no open threads.
- **Review can post before a required diagram fails** → no completion marker advances, and dedupe makes the retry safe.
- **Legacy PRs lack diagrammed markers** → perform one full review after upgrade, then establish the new marker without migration state.

## Migration Plan

Deploy without data migration. Existing reviewed markers continue to support review-only completion when diagrams are disabled; diagram-enabled PRs establish their first diagrammed marker on the next successful full run. Rollback ignores the new diagram marker and resumes the prior reviewed-SHA behaviour.
