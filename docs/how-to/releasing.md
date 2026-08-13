---
description: Maintainer guide to publishing lgtmaybe with release-please, PyPI, GHCR, Homebrew, a Windows executable, and winget.
search:
  exclude: true
---

# Releasing lgtmaybe (maintainers)

Releases are automated by **release-please** (`.github/workflows/release-please.yml`).
It reads the **conventional commits** merged to `main` and keeps a "Release PR"
open that bumps the version and regenerates `CHANGELOG.md`. **Merging that PR** is
the release: it cuts the tag and the GitHub release, then the same run publishes —
**PyPI** via trusted publishing (OIDC) and the **GHCR image** + floating major
tag (`v{major}`, currently `v2`) via the reusable `.github/workflows/release.yml`
(built-in `GITHUB_TOKEN`). No publish tokens live in secrets.

A third workflow, `.github/workflows/homebrew.yml`, regenerates the **Homebrew
formula** in the tap repo (`MattJColes/homebrew-tap`, i.e. the tap
`MattJColes/tap`) so `brew install MattJColes/tap/lgtmaybe` tracks the latest
version.
`scripts/update-homebrew-formula.sh` writes a small formula that creates a venv
and `pip install`s lgtmaybe + its dependencies from **PyPI wheels**, with no
per-dependency `resource` stanzas. litellm's tree includes Rust sdists
(tokenizers, hf-xet) that can't build in Homebrew's sandbox, so building from
source is a dead end — the wheels work. The formula declares **`preserve_rpath`**
so Homebrew keeps the wheels' `@rpath` extension-dylib ids instead of failing to
rewrite them ("Failed to fix install linkage"). It's a plain source formula —
no bottle — so it installs on any architecture and macOS version.

The release run also calls `.github/workflows/windows-exe.yml` to build and
smoke-test a portable Windows executable, then `.github/workflows/winget.yml`
to submit the new asset to winget. The executable must exist before the winget
job starts; both workflows are manually dispatchable for recovery.

The workflow:

- Is **called** by `release-please.yml` (`workflow_call`) right after a release,
  because a `release: published` event is **not** delivered for a release
  release-please cuts with the built-in `GITHUB_TOKEN` (GitHub suppresses
  downstream triggers from `GITHUB_TOKEN` to prevent recursion). A daily
  `schedule` and a manual `workflow_dispatch` (with an optional `force` to
  re-publish the same version) are the safety nets.
- **Installs the regenerated formula in CI before committing it** — a real
  `brew trust` + `brew install` of the formula gates the push, so a formula that
  doesn't install is never published. The gate trusts the tap rather than setting
  `HOMEBREW_NO_REQUIRE_TAP_TRUST`, so it exercises the same two steps the install
  guide gives users. (You can seed/verify it by hand on a Mac by running
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
- [Rotate the public App private key](#rotate-the-public-app-private-key)
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
- **Homebrew tap:** create the repo **`MattJColes/homebrew-tap`** with a
  `Formula/` directory (it can start empty — the workflow writes the formula).
  Homebrew strips the `homebrew-` prefix, so that repo is the tap
  `MattJColes/tap` and the formula inside it is `MattJColes/tap/lgtmaybe` — name
  the repo `homebrew-tap`, not `homebrew-lgtmaybe`, or the formula reads as
  `MattJColes/lgtmaybe/lgtmaybe`. Add a repo secret **`HOMEBREW_TAP_TOKEN`** to
  *this* repo: a fine-grained PAT with `contents: write` on the tap repo (the
  default `GITHUB_TOKEN` cannot push to another repository). To seed or verify
  the formula by hand on a Mac, run
  `scripts/update-homebrew-formula.sh <version> path/to/homebrew-tap/Formula/lgtmaybe.rb`.
- **winget:** fork [`microsoft/winget-pkgs`](https://github.com/microsoft/winget-pkgs).
  Create a classic GitHub PAT with the `public_repo` scope and add it to this
  repo as **`WINGET_TOKEN`**. For the first release, build/upload the Windows
  asset and run `wingetcreate new <asset-url>` manually with package id
  `MattJColes.lgtmaybe`, installer type `portable`, command alias `lgtmaybe`,
  and MIT licence metadata. Microsoft moderates a new package before it exists;
  this can take days or weeks. Until that first manifest is accepted, the
  automatic release job warns and skips the winget update rather than failing
  the other release artifacts. Once accepted, the workflow uses
  `wingetcreate update` for every later version.

## Each release

1. Merge feature/fix PRs to `main` using conventional-commit messages
   (`feat:`, `fix:`, `feat!:` / `BREAKING CHANGE:` for a major bump).
2. release-please opens or updates the **Release PR** automatically. Review the
   proposed version + changelog, then **merge it** to publish.
3. To (re)publish an existing tag to PyPI without a new release, run the
   `release-please` workflow via **workflow_dispatch** with the tag name.
4. If Windows publication needs recovery, dispatch `windows-exe` for the tag,
   then dispatch `winget` after the release asset is visible.

## Rotate the public App private key

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

## Before going public

- Dogfood lgtmaybe on its own PRs so the README example is real.
- Re-check the least-privilege IAM/WIF scopes (see the
  [Bedrock](./review-with-bedrock-oidc.md) and [Vertex](./review-with-vertex-wif.md)
  guides) before the repo goes public.
