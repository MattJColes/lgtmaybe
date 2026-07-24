# github-posting Specification

## Purpose

The GitHub REST adapter (`github/rest_gateway.py`): PR context via API only,
idempotent review posting keyed by a hidden marker, fingerprint-driven
resolve-on-fix, commit-scoped incremental review, and labels — everything
best-effort where it must never fail the review.

## Requirements

### Requirement: Context comes from the API, never a checkout

`get_pr_context` SHALL fetch the diff, metadata, and head text of reviewable
files via the REST API only — PR code is never checked out or executed, which
is what makes `pull_request_target` (secrets available) safe on fork PRs.
<!-- anchor: github.context -->

#### Scenario: fork PR is reviewed
- **WHEN** the review runs with repo secrets on a fork PR
- **THEN** no PR code is fetched other than as API-returned text

### Requirement: Posting is idempotent via a hidden marker

Reviews SHALL post as one batched REST review (inline comments + summary),
with a hidden marker comment enabling in-place updates on re-run — the marker
also carries the last-reviewed-SHA watermark that drives incremental review.
<!-- anchor: github.post-review -->

#### Scenario: review re-runs on the same PR
- **WHEN** a review already exists from a prior run
- **THEN** the summary is updated in place, not duplicated

### Requirement: Findings carry fingerprints

Each inline comment SHALL embed a hidden per-finding fingerprint derived from
path and title, keying re-run dedupe and resolve-on-fix.
<!-- anchor: github.fingerprint -->

#### Scenario: same finding, next run
- **WHEN** a re-run produces a finding whose fingerprint is already posted
- **THEN** it is not posted again

### Requirement: Unanchored findings are demoted, never guessed

A finding whose anchor matched nothing (`anchored=False`) SHALL be rendered
into the review body instead of posted inline — a wrong-line comment breaks
trust faster than a finding without a precise line. `unanchored_min_severity`
floors what is worth demoting.
<!-- anchor: github.demote -->

#### Scenario: anchor text matches no changed line
- **WHEN** a finding's line is a guess
- **THEN** it appears in the review body, not as an inline comment

### Requirement: Incremental review is commit-scoped and safe

A re-run with a watermark SHALL review only the compare-API diff since the
last-reviewed SHA; the watermark moves only on success; force-push / same
head / API failure fall back to a full review; LEFT-side findings are dropped
(their coordinates are relative to the wrong base) and resolve-on-fix is
scoped to the increment's files.
<!-- anchor: github.incremental -->

#### Scenario: a review run fails
- **WHEN** posting fails after review
- **THEN** the watermark does not move, so nothing is skipped next run

### Requirement: Resolving threads is best-effort GraphQL

When a finding is gone and GitHub marks its thread outdated, the thread SHALL
be replied to and resolved via GraphQL (the one op REST can't do) —
best-effort, never failing the review.
<!-- anchor: github.resolve-fixed -->

#### Scenario: GraphQL call errors
- **WHEN** `resolveReviewThread` fails
- **THEN** the review still completes and posts normally

### Requirement: Generated and binary files are skipped

Lockfiles, minified bundles, vendored trees, binary files, and generated LLM-index corpora (`llms.txt` / `llms-full.txt`) SHALL be excluded from review
before any path filter or cap applies.
<!-- anchor: github.reviewable -->

#### Scenario: lockfile in the diff
- **WHEN** the PR changes `uv.lock`
- **THEN** it is skipped and never counts against `max_files`

### Requirement: Labels touch only our own families

Labels SHALL derive from data the review already computed — with `pr_labels`
on: `review-effort/1-5`, `possible-security-issue`, `consider-splitting` —
and reconciliation SHALL touch only lgtmaybe's own label families —
best-effort, never failing the review.
<!-- anchor: github.labels -->

#### Scenario: repo has unrelated labels
- **WHEN** labels are reconciled
- **THEN** labels outside lgtmaybe's families are never added or removed
