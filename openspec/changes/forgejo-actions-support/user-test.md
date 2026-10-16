# Forgejo User Acceptance Test

This is the script for a tester trying lgtmaybe on Forgejo for the first time.
The point is to find out whether "out of the box" holds for someone who did not
build it. So follow the published docs page (`docs/how-to/review-on-forgejo.md`)
as written, and note every place you had to guess, search or ask. A step that
works only after a guess counts as a failure of the docs, even if the code is
fine.

Record each result in the table at the end: pass, fail, or blocked, with the
run link and a line on what you saw.

## Before You Start

You need:

- a Forgejo instance where you can create repositories, on Forgejo 11 or later
  (OpenID Connect scenarios need Forgejo 16 or later);
- a runner you can register, with the `docker` label (a Podman runner also
  works). Scenario 9 needs a second runner using the LXC or host backend;
- a model API key with a spend limit (Anthropic or OpenAI is simplest);
- for the keyless scenarios only: an AWS, Azure or GCP account where you can
  create an OIDC trust, and a Forgejo instance the cloud can reach over the
  internet. Codeberg works.

Pick one environment and write it down:

- **A. Local.** `docker compose -f tests/forgejo/uat/compose.yml up` starts
  Forgejo and a registered runner on your machine. Fastest, but it cannot do
  the keyless scenarios, because no cloud can reach `localhost`.
- **B. Codeberg with your own runner.** A real public instance. Covers
  everything.
- **C. Codeberg's shared runners.** Only for scenario 10.

Use two Forgejo accounts: a maintainer who owns the repository, and an
outsider with no write access. The outsider opens the fork pull request and
tries the slash command.

## Scenarios

Each scenario is marked **must pass** (blocks release) or **should pass** (a
known gap is acceptable if the docs say so).

### 1. First Review (Must Pass)

Follow the docs from a blank repository: token, secrets, workflow file. Then,
as the maintainer, open a pull request from a branch that adds a function with
an obvious bug (an off-by-one in a loop, or an unchecked `None`).

Expect: the run starts with no edits to the example except the model name. It
posts inline comments on the changed lines, one summary comment naming the
model, and a change overview comment. Note how long setup took and every step
where you had to stop and think.

### 2. Re-run Without Duplicates (Must Pass)

Push a commit to the same pull request that changes nothing near the bug.

Expect: the summary comment is edited, not posted again. The original finding
is not posted a second time. The change overview updates.

### 3. Clean Change (Must Pass)

Open a pull request that only fixes a typo in a README.

Expect: a `👍 LGTM!` comment naming the model. Never an approval.

### 4. Fork Pull Request (Must Pass)

As the outsider, fork the repository and open a pull request from the fork.

Expect: the review runs and posts, because `pull_request_target` gives the base
workflow its secrets. Then, in the fork, edit `.forgejo/workflows/lgtmaybe.yml`
to add `run: env` and push. Expect: the review still runs the base branch's
workflow, and no secret appears in the log.

### 5. Slash Commands (Must Pass)

As the maintainer, comment `/describe` and then `/ask what does this change
do?` on the pull request from scenario 1. As the outsider, comment `/review`.

Expect: the maintainer's commands get replies. The outsider's command logs
"ignored: no write access", exits successfully, and makes no model call. Check
the model provider's usage page to confirm the last part.

### 6. Merge Gate (Should Pass)

Set `INPUT_FAIL_ON: high` in the workflow and re-run scenario 1.

Expect: a failing commit status on the pull request. If you have a branch
protection rule requiring it, the merge button is blocked.

### 7. Failure Is Loud (Must Pass)

Change the model secret to a wrong value and re-run.

Expect: a short "lgtmaybe review failed" comment on the pull request and a red
run. Never a silent pass or an LGTM. Put the right key back afterwards.

### 8. Keyless Cloud (Must Pass on Environment B, for at Least One Cloud)

Follow the docs' keyless section for one cloud: Bedrock, Azure or Vertex.
Create the trust from the issuer and subject the docs give, remove the model
API key secret, and re-run scenario 1.

Expect: the review posts with no cloud key stored anywhere. Then repeat with
`enable-openid-connect: true` removed from the workflow. Expect: the run fails
with a message telling you to add it.

Record which cloud, the Forgejo version, which `sub` form your repository uses,
and whether the docs got the trust policy right first time.

### 9. LXC or Host Runner (Should Pass)

On a runner without Docker, follow the docs' pip fallback.

Expect: the same result as scenario 1.

### 10. Codeberg Shared Runners (Should Pass)

On Codeberg, switch `runs-on` to a Codeberg shared runner label and re-run
scenario 1.

Expect: either it works, or the docs tell you in advance that it does not and
send you to the pip fallback. Record which happened.

### 11. Automatic Token (Should Pass)

Change the token mapping to `GITEA_TOKEN: ${{ secrets.FORGEJO_TOKEN }}` and
re-run scenarios 1 and 2.

Expect: record what happens. This scenario answers an open design question, so
"it does not work" is a useful result, not a failure. Note whether comments
post, which account they post as, and whether scenario 2 still avoids
duplicates.

## Afterwards

Tell us in a few sentences:

- how long setup took, start to first review;
- the one step you would change in the docs;
- anything lgtmaybe posted that was wrong, confusing or noisy.

## Results

| # | Scenario | Bar | Environment | Result | Run link | Notes |
|---|---|---|---|---|---|---|
| 1 | First review | must | | | | |
| 2 | Re-run without duplicates | must | | | | |
| 3 | Clean change | must | | | | |
| 4 | Fork pull request | must | | | | |
| 5 | Slash commands | must | | | | |
| 6 | Merge gate | should | | | | |
| 7 | Failure is loud | must | | | | |
| 8 | Keyless cloud | must (B) | | | | |
| 9 | LXC or host runner | should | | | | |
| 10 | Codeberg shared runners | should | | | | |
| 11 | Automatic token | should | | | | |
