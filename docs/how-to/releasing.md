---
description: Maintainer guide to publishing lgtmaybe with release-please, PyPI, GHCR, Homebrew, a Windows executable, and winget.
search:
  exclude: true
---

# Releasing lgtmaybe (Maintainers)

**release-please** automates releases
(`.github/workflows/release-please.yml`). It reads the **conventional commits**
merged to `main`. From them it keeps a Release PR open that bumps the version
and regenerates `CHANGELOG.md`.

**Merging that PR** is the release. release-please creates the
`lgtmaybe-vX.Y.Z` tag and the GitHub release. The same run then publishes
everywhere:

- **PyPI**, through trusted publishing ([OIDC](../reference/glossary.md#oidc))
  in an inline job.
- **GHCR image and Action tags**, through the reusable
  `.github/workflows/release.yml`, using the built-in `GITHUB_TOKEN`.
    - It pushes a multi-arch (amd64, arm64) image tagged `X.Y.Z`, `vX`
      (currently `v2`), and `latest`.
    - It moves the git tags `vX.Y.Z`, `vX`, and `lgtmaybe-vX` to the release
      commit, so workflows can use `MattJColes/lgtmaybe@v2`.
- **Homebrew**, through `.github/workflows/homebrew.yml`, after the PyPI job
  succeeds (described below).
- **Windows**, through two workflows:
    - `.github/workflows/windows-exe.yml` builds and smoke-tests a portable
      executable. It updates Defender, scans the file, and attaches it only
      after those checks pass.
    - `.github/workflows/winget.yml` then submits that asset to winget. The
      winget job waits for the executable job.

    You can dispatch both workflows by hand for recovery.

PyPI and GHCR need no publish tokens in secrets. Homebrew and winget each need
one PAT (personal access token), set up below.

Commit messages must follow the conventional-commit format.
`.github/workflows/commitlint.yml` enforces it on PRs, so release-please can
work out the next version.

## Contents

- [Stacked Pull Requests](#stacked-pull-requests)
- [The Homebrew Formula](#the-homebrew-formula)
- [One-Time Setup](#one-time-setup)
- [Each Release](#each-release)
- [Defender Checks and Recovery](#defender-checks-and-recovery)
- [Rotate the Public App Private Key](#rotate-the-public-app-private-key)
- [Before Going Public](#before-going-public)

## Stacked Pull Requests

release-please only sees commits that reach `main`. A stacked PR targets the
branch of the PR below it, so it can miss `main` entirely:

1. The bottom PR squash-merges into `main`. Its branch stays behind.
2. GitHub retargets the PRs above it only when that branch is deleted.
3. An upper PR merged now lands on the old branch, not `main`. Its
   conventional commit never reaches the changelog.

`.github/workflows/stack-retarget.yml` stops this. When a PR merges, it moves
every open PR based on the merged branch onto the merged PR's own base, and
comments on each one. It runs main's copy of the workflow, so it also covers
layers whose branches were cut before it existed. Merge the new base into a retargeted branch before you
merge it, so its diff shows only that layer.

GitHub starts no workflow for a merge made with another workflow's
`GITHUB_TOKEN`. After a merge like that, check the base of the layers above it.

If a layer has already landed on a dead branch, open one PR that brings that
branch into `main`. Its squash commit is a single commit, so list every
stranded layer in the PR body between `BEGIN_COMMIT_OVERRIDE` and
`END_COMMIT_OVERRIDE`. release-please then reads those lines instead of the
squash message:

```text
BEGIN_COMMIT_OVERRIDE
fix(engine): keep scan findings on files triage dropped (#789)

fix: escape the describe fallback text before posting it (#791)
END_COMMIT_OVERRIDE
```

Write the marker words only in the block itself. The tool reads from the first
`BEGIN_COMMIT_OVERRIDE` in the body. If the words appear earlier in the
description, it parses the wrong text and skips the commit.

You can edit the override after the merge. release-please reads it on its next
run, which is the next push to `main`.

## The Homebrew Formula

`homebrew.yml` regenerates the formula in the tap repo
(`MattJColes/homebrew-tap`, which is the tap `MattJColes/tap`). That keeps
`brew install MattJColes/tap/lgtmaybe` on the latest version.

`scripts/update-homebrew-formula.sh` writes a small formula. The formula
creates a venv and `pip install`s lgtmaybe and its dependencies from **PyPI
wheels**. It has no per-dependency `resource` stanzas. Building from source is a
dead end: litellm's dependency tree includes Rust sdists (tokenizers, hf-xet)
that can't build in Homebrew's sandbox.

The formula declares **`preserve_rpath`**. Homebrew then keeps the wheels'
`@rpath` extension-dylib ids instead of failing to rewrite them ("Failed to fix
install linkage"). It is a plain source formula with no bottle (no prebuilt
package), so it installs on any architecture and macOS version.

The workflow:

- Is **called** by `release-please.yml` (`workflow_call`) once the PyPI publish
  succeeds. GitHub does not deliver a `release: published` event for a release
  cut with the built-in `GITHUB_TOKEN`. It suppresses downstream triggers to
  prevent recursion.
- Also runs:
    - on `release: published`, for a release a human cuts;
    - on a daily `schedule`;
    - on a manual `workflow_dispatch`, with an optional `force` to re-publish
      the same version.
- **Installs the regenerated formula in CI before committing it.** A real
  `brew trust` + `brew install` gates the push, so a formula that doesn't
  install is never published. The gate trusts the tap rather than setting
  `HOMEBREW_NO_REQUIRE_TAP_TRUST`. That way it runs the same steps the install
  guide gives users.

A new version reaches the tap within minutes of release. There is no PyPI
cooldown to wait out, because the workflow doesn't use
`brew update-python-resources` (which imposes one).

## One-Time Setup

- **PyPI:** add a **trusted publisher** for this repo, with workflow
  **`release-please.yml`** and environment `pypi`. There is no `PYPI_TOKEN`
  secret; auth is via OIDC. The publish job is inline in that workflow on
  purpose. PyPI trusted publishing requires the OIDC `job_workflow_ref` to equal
  the top-level workflow.
- Create the repo **environment** named `pypi` (Settings → Environments).
- After the first release, set the **GHCR package visibility to public**
  (Packages → lgtmaybe → Package settings). Consumers can then `docker pull` the
  image.
- First release only: from the GitHub release page, tick **"Publish this Action
  to the GitHub Marketplace"** and accept the terms. Pick the categories
  `code-review` and `continuous-integration`.
- **Homebrew tap:** create the repo **`MattJColes/homebrew-tap`** with a
  `Formula/` directory.
    - It can start empty; the workflow writes the formula.
    - Homebrew strips the `homebrew-` prefix. So that repo is the tap
      `MattJColes/tap`, and the formula inside it is `MattJColes/tap/lgtmaybe`.
    - Name the repo `homebrew-tap`, not `homebrew-lgtmaybe`, or the formula
      reads as `MattJColes/lgtmaybe/lgtmaybe`.
    - Add a repo secret **`HOMEBREW_TAP_TOKEN`** to *this* repo. It is a
      fine-grained PAT with `contents: write` on the tap repo. The default
      `GITHUB_TOKEN` cannot push to another repository.
    - To seed or verify the formula by hand on a Mac, run
      `scripts/update-homebrew-formula.sh <version> path/to/homebrew-tap/Formula/lgtmaybe.rb`.
- **winget:** fork [`microsoft/winget-pkgs`](https://github.com/microsoft/winget-pkgs).
  Create a classic GitHub PAT with the `public_repo` **and `workflow`** scopes.
  Add it to this repo as **`WINGET_TOKEN`**. Here is why it needs `workflow`:
    - `wingetcreate` syncs the fork with upstream before it opens each PR.
    - `microsoft/winget-pkgs` often changes files under `.github/workflows/`.
    - GitHub refuses a fork sync that brings in workflow-file changes unless the
      token has the `workflow` scope.

    With `public_repo` alone, the submission fails on almost every release.

- **First winget release:** build and upload the Windows asset. Then run
  `wingetcreate new <asset-url>` by hand with:
    - package id `MattJColes.lgtmaybe`;
    - installer type `portable`;
    - command alias `lgtmaybe`;
    - MIT licence metadata.

    Microsoft moderates a new package before it is published, which can take
    days or weeks. Until that first manifest is accepted, the release job warns
    and skips the winget update. It does not fail the other release artefacts.
    After that, the workflow runs `wingetcreate update` for every new version.

## Each Release

1. Merge feature/fix PRs to `main` using conventional-commit messages (`feat:`,
   `fix:`, `feat!:` / `BREAKING CHANGE:` for a major bump).
2. release-please opens or updates the **Release PR** automatically. Review the
   proposed version + changelog, then **merge it** to publish.
    - Once the release is cut, the `contributors` job appends a
      **Contributors** section to the GitHub release.
    - That section names the human authors of the release's pull requests and
      welcomes first-time contributors. Bots are left out.
    - `scripts/release_contributors.py` builds it, fed by GitHub's
      generate-notes API.
    - `CHANGELOG.md` is not touched.
    - The merge also republishes the docs, so the homepage's What's New list
      picks up the release.
3. To (re)publish an existing tag to PyPI without a new release, run the
   `release-please` workflow via **workflow_dispatch** with the tag name (for
   example `lgtmaybe-v2.11.1`). To rebuild the GHCR image and move the Action
   tags for an existing tag, dispatch `release` the same way.
4. If Windows publication needs recovery, dispatch `windows-exe` for the tag.
   Set `force` to replace an existing asset. Then dispatch `winget` once the
   release asset is visible.
5. The `winget` job may fail with "The forked repository could not be synced
   with the upstream commits". If so, sync `MattJColes/winget-pkgs` with
   upstream. Use **Sync fork** on its `master`, or run
   `gh api -X POST repos/MattJColes/winget-pkgs/merge-upstream -f branch=master`.
   Then dispatch `winget` again with the version. If it keeps recurring, check
   that `WINGET_TOKEN` still carries the `workflow` scope.

## Defender Checks and Recovery

```bash
gh workflow run windows-defender.yml -f version=2.15.3
```

Run this from the repository after the workflow reaches `main`. It downloads
the existing release file on a Windows runner. It does not rebuild or replace
the executable. You can also select **Windows Defender release check** in
Actions and choose **Run workflow**.

The build scans new executables before upload. The winget workflow scans the
published file again before submission, including manual retries. Both checks
update Defender's signatures first. A detection, scan error, or failed update
blocks publication or submission.
Defender stays active while smoke tests unpack and run the executable.

The packaging environment compiles PyInstaller's launcher from the locked source
package on each build. The bundle also disables optional UPX compression. These
steps reduce common packaging triggers for antivirus false positives.
[PyInstaller's build guide](https://pyinstaller.org/en/stable/bootloader-building.html)
describes the launcher build. The **Windows packaging** PR check builds, scans,
and smoke-tests changes to the release recipe before merge.

Open the run summary for the verdict and SHA-256 hash. Download its
`windows-defender-*` artefact for:

- `scan.log`, containing the scan output and any detection name;
- `update*.log`, containing update and status output, including retries;
- `result.json`, containing the hash, exit code, and engine and signature versions;
- `report.md`, containing the result and next steps.

A clean scan does not guarantee that winget's installation tests will pass.
Defender can also detect files extracted or run during installation.

When a check fails, inspect its logs and fix the packaging or scanner error.
Build and scan the corrected executable in CI before publishing it. If the
release already exists, use a new version so the winget manifest identifies
the corrected bytes. Then dispatch `winget` with that version.

## Rotate the Public App Private Key

Rotate the public `lgtmaybe` GitHub App key:

- quarterly;
- whenever maintainer access changes;
- immediately after any suspected exposure.

Keep the old key active until the replacement has passed a brokered smoke test.
That way rotation does not interrupt reviews.

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
5. Delete the old key in the GitHub App settings. Then delete the downloaded PEM
   from the maintainer machine. Never paste the PEM into a command argument,
   issue, workflow log, or repository file.

If the smoke test fails:

1. Leave the old GitHub key active.
2. Put its PEM back into the same Secrets Manager secret.
3. Refresh the Lambda configuration again.
4. Investigate before retrying.

## Before Going Public

- Dogfood lgtmaybe on its own PRs so the README example is real.
- Re-check the least-privilege [IAM](../reference/glossary.md#iam)/[WIF](../reference/glossary.md#wif)
  scopes before the repo goes public. See the
  [Bedrock](./review-with-bedrock-oidc.md) and [Vertex](./review-with-vertex-wif.md)
  guides.
