# Releasing lgtmaybe (maintainers)

Releases are automated by **release-please** (`.github/workflows/release-please.yml`).
It reads the **conventional commits** merged to `main` and keeps a "Release PR"
open that bumps the version and regenerates `CHANGELOG.md`. **Merging that PR** is
the release: it cuts the tag and the GitHub release, then the same run publishes —
**PyPI** via trusted publishing (OIDC) and the **GHCR image** + floating major
tag (`v{major}`, currently `v0`) via the reusable `.github/workflows/release.yml`
(built-in `GITHUB_TOKEN`). No publish tokens live in secrets.

A third workflow, `.github/workflows/homebrew.yml`, fires on the published
release and regenerates the **Homebrew formula** in the tap repo
(`MattJColes/homebrew-lgtmaybe`) so `brew install MattJColes/lgtmaybe/lgtmaybe`
tracks the latest version. It regenerates the whole formula — including every
PyPI resource stanza via `scripts/update-homebrew-formula.sh` — so a dependency
bump is picked up too.

Commit messages must follow conventional-commit format — `.github/workflows/commitlint.yml`
enforces it on PRs so release-please can compute the next version.

The only human-only pieces:

## Contents

- [One-time setup](#one-time-setup)
- [Each release](#each-release)
- [Before going public](#before-going-public)

## One-time setup

- On PyPI, add a **trusted publisher** for this repo: workflow
  **`release-please.yml`**, environment `pypi` (no `PYPI_TOKEN` secret — auth is
  via OIDC). The publish job is inline in that workflow on purpose: PyPI trusted
  publishing requires the OIDC `job_workflow_ref` to equal the top-level workflow.
- Create the repo **environment** named `pypi` (Settings → Environments).
- After the first release, set the **GHCR package visibility to public** so
  consumers can `docker pull` the image (Packages → lgtmaybe → Package settings).
- First release only: from the GitHub release page, tick **"Publish this Action
  to the GitHub Marketplace"**, accept the terms, and pick the categories
  `code-review` and `continuous-integration`.
- **Homebrew tap:** create the repo **`MattJColes/homebrew-lgtmaybe`** with a
  `Formula/` directory (it can start empty — the workflow writes the formula).
  Add a repo secret **`HOMEBREW_TAP_TOKEN`** to *this* repo: a fine-grained PAT
  with `contents: write` on the tap repo (the default `GITHUB_TOKEN` cannot push
  to another repository). To seed or verify the formula by hand on a Mac, run
  `scripts/update-homebrew-formula.sh <version> path/to/homebrew-lgtmaybe/Formula/lgtmaybe.rb`.

## Each release

1. Merge feature/fix PRs to `main` using conventional-commit messages
   (`feat:`, `fix:`, `feat!:` / `BREAKING CHANGE:` for a major bump).
2. release-please opens or updates the **Release PR** automatically. Review the
   proposed version + changelog, then **merge it** to publish.
3. To (re)publish an existing tag to PyPI without a new release, run the
   `release-please` workflow via **workflow_dispatch** with the tag name.

## Before going public

- Dogfood lgtmaybe on its own PRs so the README example is real.
- Re-check the least-privilege IAM/WIF scopes (see the
  [Bedrock](./review-with-bedrock-oidc.md) and [Vertex](./review-with-vertex-wif.md)
  guides) before the repo goes public.
