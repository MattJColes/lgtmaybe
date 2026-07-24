---
description: Post lgtmaybe reviews under your own branded GitHub App identity (lgtmaybe[bot]) with higher API rate limits, instead of the default github-actions[bot].
---

# Post reviews as a GitHub App

By default lgtmaybe posts reviews as `github-actions[bot]` using the workflow's
built-in token. Point it at a **GitHub App** you own and reviews post under that
App's identity instead — a branded `lgtmaybe[bot]` name and avatar, a higher API
rate limit, and optional access across several repositories.

This changes only the **posting identity**. The action still runs in your own CI,
still fetches the diff via the API without checking out PR code, and the keyless
cloud auth (Bedrock/Vertex/Azure) is untouched. There is no hosted service — you
are not sending your code or keys anywhere new.

## Contents

- [How it works](#how-it-works)
- [One-time GitHub App setup](#one-time-github-app-setup)
- [Workflow example](#workflow-example)
- [Reviewing across several repositories](#reviewing-across-several-repositories)
- [Without the built-in inputs](#without-the-built-in-inputs)
- [Troubleshooting](#troubleshooting)

## How it works

You pass the App's ID and private key as action inputs. The action mints a
**short-lived installation token** with
[`actions/create-github-app-token`](https://github.com/actions/create-github-app-token),
uses it to read the PR and post the review, and revokes it in a post step at the
end of the job. Nothing long-lived is stored, and the token's scope is only what
the App is installed for.

## One-time GitHub App setup

This is the human-only part — do it once:

1. Create a **GitHub App** (Settings → Developer settings → GitHub Apps → New, at
   the personal or organisation level). Name it — the name becomes the `[bot]`
   identity on the review, e.g. `lgtmaybe`.
2. Under **Repository permissions**, grant:
   - **Pull requests: Read and write** — to post the review and inline comments.
   - **Contents: Read-only** — to read the config and files.
   - (Optional) **Issues: Read and write** if you use `/ask`, which replies on the
     PR conversation.
3. You do **not** need to subscribe to any webhook events — the App is used only
   to mint a token; the review is still triggered by your workflow.
4. **Install** the App on the repositories you want reviewed (the App's page →
   Install App).
5. Note the **App ID** (on the App's General page) and **generate a private key**
   (same page → Private keys → Generate). The key downloads as a `.pem` file.
6. Store them in the repo (or org):
   - **App ID** as a repository variable, e.g. `LGTMAYBE_APP_ID` (Settings →
     Secrets and variables → Actions → Variables).
   - **Private key** as a secret, e.g. `LGTMAYBE_APP_PRIVATE_KEY` (paste the whole
     `.pem` contents).

## Workflow example

Add `app_id` and `app_private_key` to any provider workflow. Everything else is
unchanged — this example is the anthropic workflow with the App identity added:

```yaml
name: lgtmaybe

on:
  pull_request_target:
  issue_comment:
    types: [created]

permissions:
  contents: read           # still needed for the actions/checkout of .lgtmaybe.yml
  pull-requests: write

jobs:
  review:
    # Only trusted authors can trigger a review (see "Who can trigger a review").
    if: >-
      (github.event_name == 'pull_request_target' &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.pull_request.author_association)) ||
      (github.event.issue.pull_request &&
       contains(fromJson('["OWNER", "MEMBER", "COLLABORATOR"]'), github.event.comment.author_association))
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: MattJColes/lgtmaybe@v0
        with:
          provider: anthropic
          model: claude-sonnet-4-6
          api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          app_id: ${{ vars.LGTMAYBE_APP_ID }}
          app_private_key: ${{ secrets.LGTMAYBE_APP_PRIVATE_KEY }}
```

The App's **installation** permissions govern what the review can post; the
workflow `permissions:` block still applies to the default token that
`actions/checkout` uses to read `.lgtmaybe.yml`.

## Reviewing across several repositories

By default the minted token is scoped to the current repository. To let one App
review PRs across a monorepo or several repos in an org, set `app_owner` (and
optionally `app_repositories`):

```yaml
      - uses: MattJColes/lgtmaybe@v0
        with:
          provider: anthropic
          model: claude-sonnet-4-6
          api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          app_id: ${{ vars.LGTMAYBE_APP_ID }}
          app_private_key: ${{ secrets.LGTMAYBE_APP_PRIVATE_KEY }}
          app_owner: my-org
          app_repositories: |
            service-a
            service-b
```

With `app_owner` set and `app_repositories` empty, the token covers every repo
the App is installed on under that owner.

## Without the built-in inputs

The `app_id` / `app_private_key` inputs are a convenience — because `github_token`
is already a pass-through input, you can mint the token yourself and pass it in.
This is useful if you already run `actions/create-github-app-token` for other
steps:

```yaml
      - uses: actions/create-github-app-token@v2
        id: app-token
        with:
          app-id: ${{ vars.LGTMAYBE_APP_ID }}
          private-key: ${{ secrets.LGTMAYBE_APP_PRIVATE_KEY }}
      - uses: MattJColes/lgtmaybe@v0
        with:
          provider: anthropic
          model: claude-sonnet-4-6
          api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token: ${{ steps.app-token.outputs.token }}
```

## Troubleshooting

**Reviews still post as `github-actions[bot]`** — `app_id` is empty, so the action
fell back to the default token. Check that the `LGTMAYBE_APP_ID` variable and
`LGTMAYBE_APP_PRIVATE_KEY` secret are set and referenced correctly (a variable is
`${{ vars.* }}`, a secret is `${{ secrets.* }}`).

**`RequestError [HttpError]: Not Found` / `Resource not accessible by
integration`** — the App is not installed on the repository, or lacks a required
permission. Install it on the repo and confirm **Pull requests: write** +
**Contents: read** under the App's Repository permissions (re-accept the
permission change on the installation if you edited it after installing).

**`private key ... failed to parse`** — the secret must contain the full PEM,
including the `-----BEGIN...-----` / `-----END...-----` lines. Paste the whole
`.pem` file contents.
