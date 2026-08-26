## Context

GitHub's compare API answers whether the current head is ahead of the previous
reviewed head, not whether every intervening commit belongs to the PR. A normal
"merge base into branch" update is ahead, but its comparison contains the base
branch history and can be much larger than the PR's current diff.

## Goals / Non-Goals

**Goals:**

- Never treat a merge-containing comparison as a safe incremental PR diff.
- Reuse the established full-review fallback.
- Preserve cheap incremental reviews for linear pushes.

**Non-Goals:**

- Reconstruct a minimal incremental diff across arbitrary merge topology.
- Change event selection, review markers, or GitHub Action configuration.
- Add reviewed-file telemetry as part of this correctness fix.

## Decisions

### Fall back when any compared commit has multiple parents

`compare_diff` already fetches the comparison JSON to inspect `status`. It will
also inspect the returned commits and return `None` when any commit has more
than one parent. The caller already interprets `None` as "review the full PR",
so the fix adds no new control flow or public interface.

This deliberately sends feature-branch merge commits through a full review as
well. That costs more model input but is safe; distinguishing every possible
merge topology would add complexity to a merge-gate correctness path.

## Risks / Trade-offs

- PRs that use merge commits internally receive a full review on that push.
- GitHub compare responses with missing commit metadata remain accepted when
  linear, preserving current behavior for compatible API implementations.
- Rebase updates already report `diverged` and retain their existing full-review
  fallback.

## Migration Plan

Ship as a backward-compatible gateway fix. Roll back by removing the merge
guard; no stored state or configuration migration is required.
