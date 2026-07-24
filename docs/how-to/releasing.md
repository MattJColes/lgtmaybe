---
description: Maintainer guide to cutting and publishing a new lgtmaybe release with release-please, PyPI trusted publishing, and GHCR.
search:
  exclude: true
---

# Releasing lgtmaybe (maintainers)

Releases are automated by **release-please** (`.github/workflows/release-please.yml`).
It reads the **conventional commits** merged to `main` and keeps a "Release PR"
open that bumps the version and regenerates `CHANGELOG.md`. **Merging that PR** is
the release: it cuts the tag and the GitHub release, then the same run publishes —
**PyPI** via trusted publishing (OIDC) and the **GHCR image** + floating major
tag (`v{major}`, currently `v1`) via the reusable `.github/workflows/release.yml`
(built-in `GITHUB_TOKEN`). No publish tokens live in secrets.

A third workflow, `.github/workflows/homebrew.yml`, regenerates the **Homebrew
formula** in the tap repo (`MattJColes/homebrew-lgtmaybe`) so
`brew install MattJColes/lgtmaybe/lgtmaybe` tracks the latest version.
`scripts/update-homebrew-formula.sh` writes a small formula that creates a venv
and `pip install`s lgtmaybe + its dependencies from **PyPI wheels**, with no
per-dependency `resource` stanzas. litellm's tree includes Rust sdists
(tokenizers, hf-xet) that can't build in Homebrew's sandbox, so building from
source is a dead end — the wheels work. The formula declares **`preserve_rpath`**
so Homebrew keeps the wheels' `@rpath` extension-dylib ids instead of failing to
rewrite them ("Failed to fix install linkage"). It's a plain source formula —
no bottle — so it installs on any architecture and macOS version.

The workflow:

- Is **called** by `release-please.yml` (`workflow_call`) right after a release,
  because a `release: published` event is **not** delivered for a release
  release-please cuts with the built-in `GITHUB_TOKEN` (GitHub suppresses
  downstream triggers from `GITHUB_TOKEN` to prevent recursion). A daily
  `schedule` and a manual `workflow_dispatch` (with an optional `force` to
  re-publish the same version) are the safety nets.
- **Installs the regenerated formula in CI before committing it** — a real
  `brew install` of the formula gates the push, so a formula that doesn't install
  is never published. (You can seed/verify it by hand on a Mac by running
  `scripts/update-homebrew-formula.sh <version> path/to/Formula/lgtmaybe.rb`.)

Net effect: a new version lands in the tap within minutes of release — no PyPI
cooldown to wait out, since `brew update-python-resources` (which imposes one)
isn't used.

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
