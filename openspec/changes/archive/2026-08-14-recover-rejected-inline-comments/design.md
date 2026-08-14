## Context

The first review batches its summary and inline comments in one REST request.
Later runs update that review body, then create only new inline comments through
`POST /pulls/{number}/comments`. GitHub may reject an individual position with
422 even after lgtmaybe's local diff validation accepts it.

## Goals / Non-Goals

**Goals:**

- Preserve every rerun finding when GitHub rejects its inline placement.
- Let valid later comments post after one rejected comment.
- Retain enough sanitized response detail to diagnose GitHub validation rules.

**Non-Goals:**

- Recover a failed first-review batch.
- Retry an unchanged 422 payload.
- Change local anchoring, dedupe identities, or non-422 error handling.

## Decisions

### Demote only the rejected finding

The rerun helper will keep each comment paired with its `ReviewFinding`. A 422
is collected and posting continues; every other unsuccessful response still
raises. After the posting pass, the gateway updates the review body once more
with the rejected findings added to the existing demoted findings.

This reuses the established body rendering, intentionally dropping an inline
suggestion that GitHub cannot place. A recovered run may advance its watermark
because every finding is then visible either inline or in the review body.

### Log the boundary response, not model prose

The warning will include path, line, side, HTTP status, and GitHub's parsed
`message` and `errors` fields. It will not include request headers, tokens, the
full request payload, or the finding body.

## Risks / Trade-offs

- GitHub also uses 422 for abuse detection. Demoting the finding still preserves
  the result, and the logged response distinguishes that case for follow-up.
- A second review-body update adds one request only when recovery is needed.
- If that fallback update fails, the review still fails loudly rather than
  claiming the rejected finding was delivered.

## Migration Plan

Ship as a backward-compatible adapter change with no migration. Roll back by
restoring fail-fast handling for individual rerun comments.
