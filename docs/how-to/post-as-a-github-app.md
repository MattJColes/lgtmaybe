---
description: Post lgtmaybe reviews as lgtmaybe[bot] with the public App, or use a self-managed GitHub App.
---

# Post as lgtmaybe[bot]

lgtmaybe is a GitHub Action. The Action runs the reviewer and keeps the
provider, model, and provider authentication in the workflow's `with:` block.
A GitHub App is optional: it changes the author of GitHub comments from
`github-actions[bot]` to a branded bot.

There are three identity choices:

- Do nothing: the Action uses GitHub's workflow token and posts as
  `github-actions[bot]`.
- Install the public lgtmaybe App: the Action posts as `lgtmaybe[bot]`; you do
  not receive or manage an App private key.
- Use your own GitHub App: provide its App ID and private key as an advanced,
  self-managed setup.

## Install the public lgtmaybe App

1. [Install the lgtmaybe App](https://github.com/apps/lgtmaybe/installations/new)
   and select only the repositories where it should review pull requests.
2. Add `id-token: write` to the workflow permissions.
3. Set `github_identity: lgtmaybe` on the Action step.

Here is a complete OpenAI workflow:

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
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7 # base repository only
      - uses: MattJColes/lgtmaybe@v1
        with:
          github_identity: lgtmaybe
          provider: openai
          model: gpt-5.5
          api_key: ${{ secrets.OPENAI_API_KEY }}
```

The provider configuration is unchanged. `github_identity` selects who posts
on GitHub; it does not select or host the model.

The public App has only:

- `contents: read`, to fetch repository and pull-request content
- `pull requests: write`, to post reviews, comments, and labels
- `issues: write`, to answer pull-request issue comments

The Action exchanges GitHub's signed workflow identity for a short-lived App
token restricted to the triggering repository and those exact permissions.
The identity broker verifies the repository, event, and default-branch
workflow. It never receives your diff, provider key, prompt, model response, or
review findings.

Selecting `github_identity: lgtmaybe` is explicit: if `id-token: write` is
missing, the App is not installed on the repository, the workflow is not
trusted, or the identity service is unavailable, the job fails with setup
guidance. A misconfiguration is never papered over with `github-actions[bot]`.

Reviews, `/review`, `/ask`, `/describe`, and `/diagram` arrive on
`pull_request_target` or `issue_comment`, which run from the default branch, so
they post as `lgtmaybe[bot]`. Replies inside review threads do not start a model
run; a pushed fix is verified by the next incremental review.

To remove access, uninstall the App from the repository or remove that
repository from the installation. Remove `github_identity: lgtmaybe` and
`id-token: write` from the workflow to return to `github-actions[bot]`. The
Action also revokes its temporary App token after each run; GitHub expires it
automatically if cleanup cannot run.

The public App intentionally has no `Checks: write` permission, so
`github_identity: lgtmaybe` cannot be combined with `fail_on`. Keep the
default `github_identity: actions` and grant the workflow `checks: write`, or
use a self-managed App with `Checks: write`, when lgtmaybe must create a merge
gate.

## Use your own GitHub App

Use this path only when your organisation already operates a GitHub App and
wants reviews attributed to it.

Configure the App with repository permissions `contents: read`, `pull
requests: write`, and `issues: write`. Add `checks: write` only when using
`fail_on`. Then install it on the repositories it may review. Store its ID as
the `LGTMAYBE_APP_ID` repository variable and its PEM private key as the
`LGTMAYBE_APP_PRIVATE_KEY` Actions secret:

```yaml
permissions:
  contents: read

steps:
  - uses: actions/checkout@v7
  - uses: MattJColes/lgtmaybe@v1
    with:
      provider: openai
      model: gpt-5.5
      api_key: ${{ secrets.OPENAI_API_KEY }}
      app_id: ${{ vars.LGTMAYBE_APP_ID }}
      app_private_key: ${{ secrets.LGTMAYBE_APP_PRIVATE_KEY }}
```

Do not combine `app_id` / `app_private_key` with
`github_identity: lgtmaybe`. The Action validates this before minting a token.
