---
description: Post lgtmaybe reviews as lgtmaybe[bot] with the public App, or use a self-managed GitHub App.
---

# Post as lgtmaybe[bot]

lgtmaybe is a GitHub Action. The Action runs the reviewer, and the provider,
model, and provider credentials stay in the workflow's `with:` block. A GitHub
App is optional: it only changes the author of lgtmaybe's GitHub comments from
`github-actions[bot]` to a branded bot.

You have three choices:

- **Do nothing:** the Action uses GitHub's workflow token and posts as
  `github-actions[bot]`.
- **Install the public lgtmaybe App:** the Action posts as `lgtmaybe[bot]`. You
  never receive or manage an App private key.
- **Use your own GitHub App:** an advanced, self-managed setup where you supply
  its App ID and private key.

## Install the public lgtmaybe App

1. [Install the lgtmaybe App](https://github.com/apps/lgtmaybe/installations/new)
   and select only the repositories where it should review pull requests.
2. Add `id-token: write` to the workflow permissions.
3. Set `github_identity: lgtmaybe` on the Action step.

Here is a complete OpenAI workflow. It gates reviews on trusted authors and
slash commands, as explained in
[Who can trigger a review](use-as-github-action.md#who-can-trigger-a-review):

```yaml
name: lgtmaybe

on:
  pull_request_target:
  issue_comment:
    types: [created]

permissions:
  contents: read
  id-token: write

jobs:
  review:
    if: >-
      (github.event_name == 'pull_request_target' &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.pull_request.author_association)) ||
      (github.event.issue.pull_request &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association) &&
       (contains(github.event.comment.body, '/review') ||
        contains(github.event.comment.body, '/improve') ||
        contains(github.event.comment.body, '/ask') ||
        contains(github.event.comment.body, '/describe') ||
        contains(github.event.comment.body, '/diagram')))
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7 # base repository only
      - uses: MattJColes/lgtmaybe@v2
        with:
          github_identity: lgtmaybe
          provider: openai
          model: gpt-5.5
          api_key: ${{ secrets.OPENAI_API_KEY }}
```

The provider configuration is unchanged: `github_identity` chooses who posts
on GitHub, not which model runs or where.

The public App has only:

- `contents: read`, to fetch repository and pull-request content
- `pull requests: write`, to post reviews, comments, and labels
- `issues: write`, to answer pull-request issue comments

The Action exchanges GitHub's signed workflow identity token for a short-lived
App token limited to the triggering repository and exactly those permissions.
The identity broker checks the repository, the event, and that the workflow
runs from the default branch. It never receives your diff, provider key,
prompt, model response, or review findings.

`github_identity: lgtmaybe` is an explicit choice. If `id-token: write` is
missing, the App is not installed on the repository, the workflow is not
trusted, or the identity service is unavailable, the job fails with setup
guidance. A misconfiguration is never papered over by quietly posting as
`github-actions[bot]`.

Reviews and the `/review`, `/improve`, `/ask`, `/describe`, and `/diagram`
commands arrive on `pull_request_target` or `issue_comment`, which run from the
default branch, so they post as `lgtmaybe[bot]`. Replies inside review threads
do not start a model run; the next review checks a pushed fix.

To remove access, uninstall the App or remove the repository from the
installation. To return to `github-actions[bot]`, remove
`github_identity: lgtmaybe` and `id-token: write` from the workflow (and add
back `pull-requests: write`). The Action revokes its temporary App token after
each run, and GitHub expires it anyway if that cleanup cannot run.

The public App intentionally has no `Checks: write` permission, so
`github_identity: lgtmaybe` cannot be combined with `fail_on`. When lgtmaybe
must create a merge gate, keep the default `github_identity: actions` and grant
the workflow `checks: write`, or use a self-managed App with `Checks: write`.
The public App also cannot resolve fixed conversations: GitHub refuses
`resolveReviewThread` for it, so those threads stay open. See
[What the public App cannot do](use-as-github-action.md#what-the-public-app-cannot-do).

## Use your own GitHub App

Use this path only when your organisation already runs a GitHub App and wants
reviews attributed to it.

Give the App the repository permissions `contents: read`,
`pull requests: write`, and `issues: write`, plus `checks: write` if you use
`fail_on`. Install it on the repositories it may review. Store its ID as the
`LGTMAYBE_APP_ID` repository variable and its PEM private key as the
`LGTMAYBE_APP_PRIVATE_KEY` Actions secret:

```yaml
permissions:
  contents: read

steps:
  - uses: actions/checkout@v7
  - uses: MattJColes/lgtmaybe@v2
    with:
      provider: openai
      model: gpt-5.5
      api_key: ${{ secrets.OPENAI_API_KEY }}
      app_id: ${{ vars.LGTMAYBE_APP_ID }}
      app_private_key: ${{ secrets.LGTMAYBE_APP_PRIVATE_KEY }}
```

The Action mints a short-lived token for the current repository from these and
revokes it at the end of the job. To review across repositories, set
`app_owner` and `app_repositories` (see the
[Action inputs](use-as-github-action.md#github-identity-and-runtime)).

Do not combine `app_id` / `app_private_key` with
`github_identity: lgtmaybe`. The Action checks this before minting a token.
