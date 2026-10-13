## Why

GitLab was the one forge whose gaps were "not yet" rather than "cannot":
every pipeline re-reviewed the whole merge request, and a 👎 on a finding was
never read back. Both cost GitLab users money and noise that GitHub users do
not pay. Thread resolution was also unreachable in practice: follow-up
validation only runs on incremental reviews, and GitLab reported every
discussion as not outdated, so every "fixed" verdict was downgraded.

## What Changes

- `GitLabGateway` claims `SupportsIncremental`: the summary note carries the
  shared `lgtmaybe-reviewed` watermark, the increment comes from the
  repository compare API reassembled into a unified diff, and every case the
  increment cannot be trusted falls back to a full review.
- `GitLabGateway` derives the "outdated" signal GitLab's API lacks, so
  validated fixes can resolve, scoped to the increment's files.
- `GitLabGateway` claims `SupportsFeedback`: `thumbsdown` award emoji from a
  Developer-or-above member on a finding's first note suppress it next run.
- `lgtmaybe gitlab-ci` resolves unset `incremental` to on, with
  `--no-incremental` / `LGTMAYBE_INCREMENTAL=false` as the full-review override.
- The watermark regexes move to `core/comment.py` so both adapters share them.

## Capabilities

### Modified Capabilities

- `gitlab-posting`: incremental review, derived outdated signal, scoped
  resolution, and award-emoji feedback.
- `cli-and-local`: the `gitlab-ci` auto-incremental default.
- `finding-quality`: feedback is read from the forge, not GitHub only.

## Impact

- `src/lgtmaybe/gitlab/gateway.py`, `src/lgtmaybe/cli/`, `src/lgtmaybe/core/comment.py`.
- New tests under `tests/gitlab/`.
- README, GitLab how-to, architecture and what-gets-reviewed capability tables.
