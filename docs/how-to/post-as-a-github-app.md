---
description: lgtmaybe is a GitHub Marketplace Action and uses GitHub Actions' built-in token; no separate GitHub App setup is required.
---

# No GitHub App setup required

lgtmaybe is published to GitHub Marketplace as a **GitHub Action**. Add the
Action to your workflow and GitHub Actions supplies the short-lived,
repository-scoped token it needs. Reviews post as `github-actions[bot]` by
default.

You do not need to create or install a separate GitHub App, manage a private
key, or run a hosted lgtmaybe service. Choose the model provider and
authentication method in the workflow instead.

[Use lgtmaybe as a GitHub Action](./use-as-github-action.md)

If your organisation already operates a GitHub App, the advanced `github_token`
input can use a token minted elsewhere. This is optional and is not part of the
standard setup.
