---
description: Maintainer guide to publishing lgtmaybe with release-please, PyPI, GHCR, Homebrew, a Windows executable, and winget.
search:
  exclude: true
---

# Releasing lgtmaybe (maintainers)

Releases are automated by **release-please**
(`.github/workflows/release-please.yml`). It reads the **conventional commits**
merged to `main` and keeps a Release PR open that bumps the version and
regenerates `CHANGELOG.md`. **Merging that PR** is the release: release-please
creates the `lgtmaybe-vX.Y.Z` tag and the GitHub release, and the same run then
publishes everywhere:

- **PyPI**, through trusted publishing (OIDC) in an inline job.
- **GHCR image and Action tags**, through the reusable
  `.github/workflows/release.yml` using the built-in `GITHUB_TOKEN`. It pushes
  a multi-arch (amd64, arm64) image tagged `X.Y.Z`, `vX` (currently `v2`), and
  `latest`, and moves the git tags `vX.Y.Z`, `vX`, and `lgtmaybe-vX` to the
  release commit so workflows can use `MattJColes/lgtmaybe@v2`.
- **Homebrew**, through `.github/workflows/homebrew.yml`, after the PyPI job
  succeeds (described below).
- **Windows**, through `.github/workflows/windows-exe.yml`, which builds and
  smoke-tests a portable executable and attaches it to the release, then
  `.github/workflows/winget.yml`, which submits that asset to winget. The
  winget job waits for the executable job. Both workflows can be dispatched by
  hand for recovery.

PyPI and GHCR need no publish tokens in secrets. Homebrew and winget each need
one PAT, set up below.

Commit messages must follow the conventional-commit format.
`.github/workflows/commitlint.yml` enforces it on PRs so release-please can
work out the next version.

## Contents

- [The Homebrew formula](#the-homebrew-formula)
- [One-time setup](#one-time-setup)
- [Each release](#each-release)
- [Rotate the public App private key](#rotate-the-public-app-private-key)
- [Before going public](#before-going-public)

## The Homebrew Formula

`homebrew.yml` regenerates the formula in the tap repo
(`MattJColes/homebrew-tap`, which is the tap `MattJColes/tap`) so
`brew install MattJColes/tap/lgtmaybe` tracks the latest version.
`scripts/update-homebrew-formula.sh` writes a small formula that creates a venv
and `pip install`s lgtmaybe and its dependencies from **PyPI wheels**, with no
per-dependency `resource` stanzas. litellm's dependency tree includes Rust
sdists (tokenizers, hf-xet) that can't build in Homebrew's sandbox, so building
from source is a dead end. The formula declares **`preserve_rpath`** so
Homebrew keeps the wheels' `@rpath` extension-dylib ids instead of failing to
rewrite them ("Failed to fix install linkage"). It is a plain source formula
with no bottle, so it installs on any architecture and macOS version.

The workflow:

- Is **called** by `release-please.yml` (`workflow_call`) once the PyPI publish
  succeeds, because GitHub does not deliver a `release: published` event for a
  release cut with the built-in `GITHUB_TOKEN` (it suppresses downstream
  triggers to prevent recursion). It also runs on `release: published` for a
  release a human cuts, on a daily `schedule`, and on a manual
  `workflow_dispatch` (with an optional `force` to re-publish the same
  version).
- **Installs the regenerated formula in CI before committing it.** A real
  `brew trust` + `brew install` gates the push, so a formula that doesn't
  install is never published. The gate trusts the tap rather than setting
  `HOMEBREW_NO_REQUIRE_TAP_TRUST`, so it exercises the same steps the install
  guide gives users.

A new version reaches the tap within minutes of release. There is no PyPI
cooldown to wait out, because `brew update-python-resources` (which imposes
one) isn't used.

## One-Time Setup

- On PyPI, add a **trusted publisher** for this repo: workflow
  **`release-please.yml`**, environment `pypi`. There is no `PYPI_TOKEN`
  secret; auth is via OIDC. The publish job is inline in that workflow on
  purpose, because PyPI trusted publishing requires the OIDC `job_workflow_ref`
  to equal the top-level workflow.
- Create the repo **environment** named `pypi` (Settings → Environments).
- After the first release, set the **GHCR package visibility to public** so
  consumers can `docker pull` the image (Packages → lgtmaybe → Package settings).
- First release only: from the GitHub release page, tick **"Publish this Action
  to the GitHub Marketplace"**, accept the terms, and pick the categories
  `code-review` and `continuous-integration`.
- **Homebrew tap:** create the repo **`MattJColes/homebrew-tap`** with a
  `Formula/` directory. It can start empty; the workflow writes the formula.
  Homebrew strips the `homebrew-` prefix, so that repo is the tap
  `MattJColes/tap` and the formula inside it is `MattJColes/tap/lgtmaybe`. Name
  the repo `homebrew-tap`, not `homebrew-lgtmaybe`, or the formula reads as
  `MattJColes/lgtmaybe/lgtmaybe`. Add a repo secret **`HOMEBREW_TAP_TOKEN`** to
  *this* repo: a fine-grained PAT with `contents: write` on the tap repo (the
  default `GITHUB_TOKEN` cannot push to another repository). To seed or verify
  the formula by hand on a Mac, run
  `scripts/update-homebrew-formula.sh <version> path/to/homebrew-tap/Formula/lgtmaybe.rb`.
- **winget:** fork [`microsoft/winget-pkgs`](https://github.com/microsoft/winget-pkgs).
  Create a classic GitHub PAT with the `public_repo` **and `workflow`** scopes
  and add it to this repo as **`WINGET_TOKEN`**. `wingetcreate` syncs the fork
  with upstream before it opens each PR, and `microsoft/winget-pkgs` often
  changes files under `.github/workflows/`. GitHub refuses a fork sync that
  brings in workflow-file changes unless the token has the `workflow` scope, so
  with `public_repo` alone the submission fails on almost every release.
- **First winget release:** build and upload the Windows asset, then run
  `wingetcreate new <asset-url>` by hand with package id `MattJColes.lgtmaybe`,
  installer type `portable`, command alias `lgtmaybe`, and MIT licence
  metadata. Microsoft moderates a new package before it is published, which can
  take days or weeks. Until that first manifest is accepted, the release job
  warns and skips the winget update rather than failing the other release
  artefacts. After that, the workflow runs `wingetcreate update` for every
  new version.

## Each Release

1. Merge feature/fix PRs to `main` using conventional-commit messages
   (`feat:`, `fix:`, `feat!:` / `BREAKING CHANGE:` for a major bump).
2. release-please opens or updates the **Release PR** automatically. Review the
   proposed version + changelog, then **merge it** to publish. Once the release
   is cut, the `contributors` job appends a **Contributors** section to the
   GitHub release, naming the human authors of its pull requests and welcoming
   first-time contributors (`scripts/release_contributors.py`, fed by GitHub's
   generate-notes API; bots are left out). `CHANGELOG.md` is not touched, and
   the merge also republishes the docs, so the homepage's What's New list
   picks up the release.
3. To (re)publish an existing tag to PyPI without a new release, run the
   `release-please` workflow via **workflow_dispatch** with the tag name (for
   example `lgtmaybe-v2.11.1`). To rebuild the GHCR image and move the Action
   tags for an existing tag, dispatch `release` the same way.
4. If Windows publication needs recovery, dispatch `windows-exe` for the tag
   (set `force` to replace an existing asset), then dispatch `winget` once the
   release asset is visible.
5. If the `winget` job fails with "The forked repository could not be synced
   with the upstream commits", sync `MattJColes/winget-pkgs` with upstream
   (**Sync fork** on its `master`, or
   `gh api -X POST repos/MattJColes/winget-pkgs/merge-upstream -f branch=master`),
   then dispatch `winget` again with the version. If it keeps recurring, check
   that `WINGET_TOKEN` still carries the `workflow` scope.

## Rotate the Public App Private Key

Rotate the public `lgtmaybe` GitHub App key quarterly, whenever maintainer access
changes, and immediately after any suspected exposure. Keep the old key active
until the replacement has passed a brokered smoke test so rotation does not
interrupt reviews.

1. In the GitHub App settings, generate a new private key. Do not delete the old
   key yet.
2. From the directory containing the downloaded PEM, replace the retained
   Secrets Manager value without printing the key:

    ```bash
    aws secretsmanager put-secret-value \
      --secret-id lgtmaybe/github-app/private-key \
      --secret-string file://lgtmaybe.private-key.pem \
      --region ap-southeast-2
    ```

3. Refresh the Lambda configuration so every execution environment drops its
   cached copy of the old key:

    ```bash
    FUNCTION_NAME="$(
      aws cloudformation describe-stack-resources \
        --stack-name LgtmaybeGithubAppIdentity \
        --region ap-southeast-2 \
        --query "StackResources[?ResourceType=='AWS::Lambda::Function'].PhysicalResourceId | [0]" \
        --output text
    )"
    aws lambda update-function-configuration \
      --function-name "$FUNCTION_NAME" \
      --description "GitHub App key rotated $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --region ap-southeast-2
    aws lambda wait function-updated-v2 \
      --function-name "$FUNCTION_NAME" \
      --region ap-southeast-2
    ```

4. Run a brokered dogfood review and confirm GitHub attributes it to
   `lgtmaybe[bot]`.
5. Delete the old key in the GitHub App settings, then delete the downloaded PEM
   from the maintainer machine. Never paste the PEM into a command argument,
   issue, workflow log, or repository file.

If the smoke test fails, leave the old GitHub key active, put its PEM back into
the same Secrets Manager secret, refresh the Lambda configuration again, and
investigate before retrying.

## Before Going Public

- Dogfood lgtmaybe on its own PRs so the README example is real.
- Re-check the least-privilege IAM/WIF scopes (see the
  [Bedrock](./review-with-bedrock-oidc.md) and [Vertex](./review-with-vertex-wif.md)
  guides) before the repo goes public.
