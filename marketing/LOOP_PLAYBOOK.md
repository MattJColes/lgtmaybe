# Marketing loop playbook

The repeatable protocol an agent (Claude) executes each iteration. Written to be
runnable from a fresh session: read [`STRATEGY.md`](STRATEGY.md), then
[`backlog.md`](backlog.md) and [`log.md`](log.md), then run the four steps below.
Suggested cadence: **weekly**. Run it via `/loop`, a scheduled Routine, or by
asking Claude to "run the marketing loop".

Hard constraints, every iteration:

- The agent has no social accounts and must not create any. It drafts; Matt
  posts. Deliverables are files in `marketing/drafts/` plus a short report.
- Follow the rules of engagement in STRATEGY.md, especially disclosure and
  one-venue-one-post.
- All drafts in Matt's voice per the `writing-draft-blog-posts` skill (blog
  posts get the full treatment including the AI-tell audit; comments and
  short-form get the teaser rules).

## Step 1 — Monitor (~10 min)

Run these searches and record anything new in `log.md`:

- Web search: `"CodeRabbit alternatives" 2026`, `"AI code review" open source site:reddit.com` (past week), `AI code review site:news.ycombinator.com` (past week), `lgtmaybe -site:github.com`
- Check for new roundup/comparison articles that list competitors but not
  lgtmaybe → add an outreach item to the backlog with the article URL and
  author contact if findable.
- Metrics snapshot: GitHub stars/forks (`MattJColes/lgtmaybe`), PyPI downloads
  (`pypistats.org/packages/lgtmaybe`). Append to the metrics table in `log.md`.

## Step 2 — Engage (~15 min)

From the monitoring results, pick **at most 3** live threads (Reddit, HN,
lobste.rs) where a comment from the author of lgtmaybe is the honest answer to
the question being asked — someone asking for a self-hosted reviewer, an
OIDC/no-keys setup, a CodeRabbit alternative, or hitting a problem lgtmaybe
solves. For each, draft a comment into
`marketing/drafts/comments-<date>.md` with the thread URL, the draft, and one
line on why this thread. Value first, disclosure always, no drive-by links.

If nothing qualifies, write nothing. Zero forced comments.

## Step 3 — Create (~30 min)

Take the top `ready` item from `backlog.md` and draft it:

- **Blog posts** → `marketing/drafts/<slug>.md`, full voice-skill treatment.
  Note in the draft where it should be syndicated (dev.to canonical, which
  subreddit, HN-worthy or not) and a suggested title per venue.
- **Reddit/HN posts** → same location, with title + body per venue rules
  (HN: plain paragraphs, no markdown lists; Reddit: whatever the subreddit's
  culture is — check its top posts first).
- **Outreach notes** (roundup submissions, awesome-list PRs) → draft the
  message/PR description; flag any that need Matt's account to send.
- **Docs pages** (e.g. the comparison page) → these are code-repo changes;
  draft the page and note it needs a normal PR through CI.

One item per iteration. Move it from `ready` to `drafted` in the backlog.

## Step 4 — Record and report

1. Append an entry to `log.md`: date, what was monitored/found, threads
   flagged, item drafted, metrics snapshot.
2. Update `backlog.md` statuses (`idea` → `ready` → `drafted` → `posted` →
   `done`; Matt moves things to `posted`).
3. If running in a session with Matt: end with a short report — what's ready
   for him to post, what needs his account, what changed in metrics. If running
   headless: commit the changes to a branch and open a PR titled
   `chore(marketing): loop iteration <date>` so the report is the PR body.

## What Matt does between iterations

- Review drafts, edit, post them, and mark backlog items `posted` with the URL.
- Reply to comments on live posts (the loop flags unanswered ones in Step 1).
- Veto anything — a `vetoed` status on a backlog item stops the loop touching it.
