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

#### Scenario: an incomplete run re-runs on the same PR
- **WHEN** the summary carries the hidden incomplete marker and the body update
  is an in-place edit nobody is notified about
- **THEN** the notice also posts as a PR comment, so a partial review is never
  indistinguishable from a clean one

### Requirement: Findings carry fingerprints

Each inline comment SHALL embed two hidden per-finding ids — a fingerprint
(path + title) and a prose-free identity (path + category + anchor, falling back
to the line when a finding has no anchor) — and re-run dedupe and resolve-on-fix
SHALL match on either. The fingerprint alone cannot key them: it hashes model
prose, and a model rewords the same finding between runs. An identity is not
unique within a file — two findings from one lens on identical source lines
share one — so re-run dedupe SHALL match candidates to posted comments
one-for-one rather than by set membership.
<!-- anchor: github.fingerprint -->

#### Scenario: same finding, next run
- **WHEN** a re-run produces a finding whose fingerprint is already posted
- **THEN** it is not posted again

#### Scenario: same finding, reworded
- **WHEN** a re-run reports the same code and concern in different words
- **THEN** its identity still matches, so it is neither posted again nor
  treated as fixed

#### Scenario: a second occurrence of an identical line
- **WHEN** a re-run flags one more occurrence than is already posted
- **THEN** the extra occurrence posts rather than being absorbed by its twin

### Requirement: Unanchored findings are demoted, never guessed

A finding whose anchor matched nothing (`anchored=False`) SHALL be rendered
into the review body instead of posted inline — a wrong-line comment breaks
trust faster than a finding without a precise line. `unanchored_min_severity`
floors what is worth demoting.
<!-- anchor: github.demote -->

#### Scenario: anchor text matches no changed line
- **WHEN** a finding's line is a guess
- **THEN** it appears in the review body, not as an inline comment

### Requirement: A posted finding names its lens and confidence

Every posted finding SHALL carry the lens that raised it and the reflection
auditor's confidence in its title line — the 0-10 score rendered as a
percentage — so a GitHub reader can weigh it without leaving the PR. Both halves
are omitted when absent — no category renders no badge at all, and no score
renders the lens alone — and the badge is visible prose only, never part of the
hidden fingerprint/identity markers that key re-run dedupe and resolve-on-fix.
Inline, demoted, and broad findings SHALL render it identically.
<!-- anchor: github.finding-badge -->

#### Scenario: a scored finding from a lens
- **WHEN** a finding carries a category and a confidence score
- **THEN** its title line reads `**[HIGH · security · 80%] Title**`

#### Scenario: reflection is off
- **WHEN** a finding has a category but no score
- **THEN** the confidence half is omitted rather than rendered empty

#### Scenario: a comment posted before badges existed
- **WHEN** a re-run reports a finding already posted with an unbadged title
- **THEN** it is still recognised and not posted again

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
be replied to and resolved via GraphQL (the one op REST can't do), and the
opening comment's fingerprint marker rewritten into a disjoint "resolved"
family so a finding that reappears later posts again instead of staying
suppressed by re-run dedupe. The three steps SHALL run in order of consequence:
resolve (the gate), rewrite the marker (the only chance — a resolved thread is
never revisited), then reply (cosmetic). Each is independently best-effort, and
best-effort is PER THREAD: threads are independent and resolve concurrently, so
one that fails never stops the others.
<!-- anchor: github.resolve-fixed -->

#### Scenario: GraphQL call errors
- **WHEN** `resolveReviewThread` fails
- **THEN** the review still completes and posts normally

#### Scenario: one thread of several fails
- **WHEN** several threads are fixed and one errors mid-resolve
- **THEN** the remaining threads are still replied to and resolved

#### Scenario: the identity may not resolve threads
- **WHEN** `resolveReviewThread` is refused for the configured identity
- **THEN** no reply is posted and no marker is rewritten, so the step is a no-op
  that cannot accumulate a reply on every subsequent run, and threads not yet
  started are skipped — a refusal belongs to the identity, not the thread, so it
  is retried at most once per concurrent wave rather than once per thread

#### Scenario: the reply fails after a successful resolve
- **WHEN** the thread resolves but its reply errors
- **THEN** the fingerprint marker is still rewritten — a resolved thread is never
  revisited, so leaving an active marker would suppress the finding forever

### Requirement: Downvoted findings are read from 👎 reactions

`list_downvoted_fingerprints` SHALL return the fingerprints of our findings an
authorised reviewer reacted 👎 (THUMBS_DOWN) to — read via GraphQL from each
review thread's first comment (its body marker plus the reacting users), with
each reactor's repo permission checked and failing closed, never persisted
locally. A write-access thumbs-down is the only suppress signal; an unprivileged
reaction and a resolved thread are not.
<!-- anchor: github.feedback -->

#### Scenario: an authorised reviewer downvotes a finding comment
- **WHEN** the opening comment carries our finding marker and a write-access user's 👎
- **THEN** its fingerprint is returned, to be suppressed next run

### Requirement: Resolved findings receive a GraphQL reply

`reply_in_thread` SHALL post a reply on a known review thread via the GraphQL
`addPullRequestReviewThreadReply` mutation. It SHALL be used only after
resolve-on-fix has verified that a finding disappeared and resolved the thread;
human comments SHALL NOT invoke it.
<!-- anchor: github.reply-in-thread -->

#### Scenario: a verified fix resolves a finding
- **WHEN** resolve-on-fix resolves a thread whose finding disappeared
- **THEN** `reply_in_thread` posts `✅ Looks resolved.` to that thread via GraphQL

### Requirement: Generated and binary files are skipped

Lockfiles, minified bundles, vendored trees, binary files, code-generator output (protobuf, Dart `build_runner`), snapshot corpora, and generated LLM-index corpora (`llms.txt` / `llms-full.txt`) SHALL be excluded from review
before any path filter or cap applies. Lockfiles SHALL stay scannable, so a new
lockfile joins the lockfile set rather than the generated-path patterns.
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

### Requirement: Merge-gate rides a Check Run, never approval state

With `fail_on` set, after posting the review the adapter SHALL create a
completed Check Run on the PR head SHA whose conclusion is `failure` when any
surviving finding is at or above `fail_on`, else `success` — so teams can make
it a required check in branch protection. Enforcement rides the Check Run;
approval state is never set. `fail_on` unset (default) creates no check run.
<!-- anchor: github.check-run -->

#### Scenario: a blocking finding is present
- **WHEN** `fail_on` is `high` and a finding is `high` or above
- **THEN** the Check Run is created with conclusion `failure`

#### Scenario: no finding meets the threshold
- **WHEN** `fail_on` is set and no surviving finding reaches it
- **THEN** the Check Run is created with conclusion `success`

### Requirement: App-authenticated activity carries one identity

The GitHub adapter SHALL use the selected credential uniformly for review and
summary comments, slash-command replies, thread resolution, labels,
descriptions, and diagrams. Public branded mode SHALL reject `fail_on` because
the least-privilege public App does not hold `checks: write`.
<!-- anchor: github.app-attribution -->

#### Scenario: Branded review completes
- **WHEN** a review uses a brokered lgtmaybe App installation token
- **THEN** GitHub attributes every supported write in that run to `lgtmaybe[bot]`

#### Scenario: Public branded review requests a merge gate
- **WHEN** `github_identity` is `lgtmaybe` and `fail_on` is set
- **THEN** the Action fails before token exchange with guidance to use Actions identity or a self-managed App

#### Scenario: Default review completes
- **WHEN** a review uses the built-in workflow token
- **THEN** existing `github-actions[bot]` posting behavior remains unchanged

### Requirement: lgtmaybe's own comments cannot preempt active reviews

Every workflow lgtmaybe ships or runs SHALL key its per-PR concurrency group on
the event name as well as the PR, and the dogfood workflow SHALL additionally
apply that group only to an event passing the review job's eligibility guard.
Runs of the same event for the same PR MUST retain newest-run-wins cancellation.
Posting SHALL be ordered so the review lands before any comment lgtmaybe emits.
<!-- anchor: github.workflow-concurrency -->

#### Scenario: lgtmaybe posts an automatic diagram

- **WHEN** the GitHub App comment emits an `issue_comment` workflow run
- **THEN** that run's concurrency group differs from the active
  `pull_request_target` review's, and at job scope an author failing the guard
  does not enter a group at all

#### Scenario: a newer push arrives mid-review

- **WHEN** a newer `pull_request_target` event passes the review job guard for
  the same PR
- **THEN** the newer job cancels the older review job for that PR

#### Scenario: a review is cancelled by a mis-scoped consumer group

- **WHEN** a consumer's group is not event-discriminated and lgtmaybe's own
  comment cancels the run
- **THEN** the review was already posted, because comments follow it
