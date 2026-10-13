## Context

GitHub's compare API answers "is head ahead of the watermark?" with a
`status`. GitLab's `GET /projects/:id/repository/compare` has no such field:
it returns `commits` (`git log from..to`), per-file `diffs`, and the flags
`compare_same_ref` and `compare_timeout`. An unknown `from` SHA is not a 404;
GitLab answers with empty lists.

## Decisions

### D1. Force-push detection from parent ids

The increment is trusted only when the commit list is a chain of
single-parent commits from `to` back to `from`, covering every listed commit.
A rebase or force-push leaves a chain that ends somewhere other than `from`; a
merge has two parents; an unknown SHA yields no commits. Each returns None,
mirroring GitHub's `status != "ahead"`, merge, and truncated-list rules. No
extra request (such as `merge_base`) is needed.

### D2. A withheld patch is a fallback, not a partial increment

GitLab stops listing files past an instance's diff limits without saying so,
and flags per-file patches it withheld (`too_large`, `collapsed`). Any flag,
or a file list at 500 entries (the smallest `diff_max_files` an admin can
set), falls back to a full review: an increment missing a file would present
unreviewed code as reviewed.

### D3. `straight=true`

Both the increment and the outdated check ask for `git diff from to`, so line
numbers are relative to `from` even when `from` is not an ancestor. On the
increment path ancestry is already verified, so this changes nothing there.

### D4. The watermark is kept, not erased, on failure

GitHub creates a fresh summary per completed commit; GitLab upserts one note.
An unmarked post (failure notice, partial run) carries the existing stamp
forward instead of erasing it. It never moves forward: only
`mark_reviewed(head)` on the success path writes a new SHA. Model prose is
stripped of any `lgtmaybe-reviewed` marker before the real one is appended, and
the reader takes the last match.

### D5. Only our own note is believed

`last_reviewed_sha` and `last_completed_sha` read a note only when its author
is the token's user (`GET /user`). Any lookup failure means no watermark, so a
full review. A forged note claiming the head is complete would otherwise skip
the review.

### D6. Derived outdated signal

GitLab re-positions a diff note onto each new head while its line survives,
so a note still positioned at an older head is a candidate. It is outdated
when a hunk of the straight diff from that head to ours spans its new-side
line on the old side, or its file was deleted. A pure insertion
(`@@ -5,0 +6,2 @@`) spans no old line, so it outdates none: counting the line
before it would loosen the resolve gate. The note's path is matched against
both `old_path` and `new_path`, so a rename does not hide a change. This is
hunk-level, close to GitHub's
`isOutdated`, and fails closed. Without it, validation downgraded every
"fixed" verdict on GitLab and nothing could resolve.

### D7. `gitlab-ci` auto means incremental

GitLab CI exposes no open-vs-push distinction (`CI_MERGE_REQUEST_EVENT_TYPE`
is `detached` / `merged_result` / `merge_train`). Since `run_review` already
falls back to a full review with no watermark, auto resolves to on: the first
pipeline is full and every later push is incremental. A re-run on an already
completed head is a no-op, as on GitHub. GitLab has no `/review full`, so
`--no-incremental` / `LGTMAYBE_INCREMENTAL=false` (settable on a manual
pipeline) is the override.

### D8. Feedback trusts Developer and above

`GET /projects/:id/members/all/:user_id` (inherited membership included);
`access_level >= 30` is GitHub's write. Fails closed and is cached per user.
Award emoji are fetched per finding note, concurrently.

## Non-Goals

- Resolve-on-fix on GitLab full reviews (still allowlist-only).
- Hardening GitHub's first-match watermark read (tracked separately).
