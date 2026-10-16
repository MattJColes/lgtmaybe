---
description: Post lgtmaybe reviews to Forgejo and Codeberg pull requests from Forgejo Actions - the workflow, the runner label, the token, and a fallback for runners without Docker.
---

# Review Pull Requests on Forgejo

!!! note "Preview"

    Forgejo support is new. It is tested end to end on Forgejo 17 with
    forgejo-runner 12, and the API calls on Forgejo 11. Tell us what breaks on
    your instance.

Forgejo is a fork of Gitea, and it still speaks Gitea's API. So lgtmaybe
reviews a Forgejo pull request through the same adapter it uses for Gitea. That
covers Codeberg, which runs Forgejo. The review is the same as on any other
host: the same [lenses](../reference/glossary.md#lens), the same
[reflection](../reference/glossary.md#reflection) pass, the same findings.

## Set It Up

You need Forgejo 11 or later and a runner that can run Docker containers.

1. **Create a token.** Sign in as a dedicated account with write access to the
   reviewed repository only. Go to *Settings → Applications → Access Tokens*.
   Create a token with the `write:repository`, `write:issue` and `read:user`
   scopes. [Review on Gitea](review-on-gitea.md#set-it-up) explains why each
   scope is needed. Add the token to your repository as a secret named
   `LGTMAYBE_FORGEJO_TOKEN`. Forgejo refuses secret names that start with
   `FORGEJO_`, `GITEA_` or `GITHUB_`.

2. **Add a provider key.** Add the API key for your model as a second secret,
   for example `ANTHROPIC_API_KEY`. Use a key with a spend limit.

3. **Add the workflow.** Copy
   [`examples/forgejo/lgtmaybe.yml`](https://github.com/MattJColes/lgtmaybe/blob/main/examples/forgejo/lgtmaybe.yml)
   into your repository at `.forgejo/workflows/lgtmaybe.yml`.

4. **Check the runner label.** The example says `runs-on: docker`. Forgejo
   runners have no default labels: the instance admin chooses them. `docker`
   is the usual label for a runner that runs jobs in containers. If your runner
   uses another label, change that one line. You can see each runner's labels
   under *Settings → Actions → Runners*.

Open a pull request and the review runs. The example runs lgtmaybe's container
image as the job container, with the same `action` command used on GitHub and
Gitea. lgtmaybe reads `FORGEJO_SERVER_URL` to know which instance to post to.
It reads the event from `GITHUB_EVENT_NAME` and `GITHUB_EVENT_PATH`, or from
their `FORGEJO_` names when a runner sets only those.

## Use the Automatic Token Instead

Forgejo gives every job its own token, `secrets.FORGEJO_TOKEN`. It can post
reviews, so you can skip step 1 by mapping it in the workflow:

```yaml
GITEA_TOKEN: ${{ secrets.FORGEJO_TOKEN }}
```

The trade-off is slash commands. lgtmaybe runs a command only for a commenter
with write access, and the automatic token cannot read anyone's permission. So
every `/review`, `/ask` or other command is ignored, and the job log says why.
Reviews post as the `forgejo-actions` user rather than your bot account.

## Keyless Bedrock, Azure and Vertex

Forgejo Actions can give a job an [OIDC](../reference/glossary.md#oidc) ID
token, as GitHub Actions does. lgtmaybe exchanges it for short-lived cloud
credentials, so no cloud key is stored anywhere. It needs Forgejo 16 or later,
and an instance the cloud can reach over the internet. The cloud fetches the
instance's signing keys to check each token, so a private instance cannot do
this. Use an API key or ollama there instead.

Add `enable-openid-connect: true` to the job, next to `runs-on`, and set the
inputs for one cloud. lgtmaybe requests the token with the audience that cloud
expects:

| Cloud | Inputs | Token audience |
|---|---|---|
| Bedrock | `INPUT_AWS_ROLE_ARN`, `INPUT_AWS_REGION` | `sts.amazonaws.com` |
| Azure | `INPUT_AZURE_CLIENT_ID`, `INPUT_AZURE_TENANT_ID` | `api://AzureADTokenExchange` |
| Vertex | `INPUT_GCP_WIF_PROVIDER`, optional `INPUT_GCP_SERVICE_ACCOUNT` | `https://iam.googleapis.com/<provider>` |

[`examples/forgejo/lgtmaybe-bedrock.yml`](https://github.com/MattJColes/lgtmaybe/blob/main/examples/forgejo/lgtmaybe-bedrock.yml)
is a complete Bedrock workflow. If OpenID Connect is off, or an input is
missing its partner, the run fails before any model call and names the fix.

On the cloud side, follow the setup in
[Bedrock](review-with-bedrock-oidc.md#one-time-aws-setup),
[Azure](review-with-azure.md#one-time-azure-setup) or
[Vertex](review-with-vertex-wif.md#one-time-gcp-setup), with Forgejo's values
in place of GitHub's:

- **Issuer:** `https://<your instance>/api/actions`, for example
  `https://codeberg.org/api/actions`.
- **Subject:** the review runs from your default branch, so the `sub` claim is
  `repo:<repository>:ref:refs/heads/main` (with your default branch's name).
  Trust exactly that value.
- **Repository:** for a repository that enabled Actions on Forgejo 16 or later,
  `<repository>` uses ids, such as `alice-12/service-34`. Older repositories use
  `alice/service`. Read the claim from a test token rather than guessing it.

For Vertex, also set `VERTEXAI_PROJECT` (and `VERTEXAI_LOCATION` for a region
other than `us-central1`) in the step's `env:`. Unlike the GitHub Action, the
job container passes them through.

The cloud credentials last about an hour, which covers the default review time
limit. A review that runs longer fails when they expire.

## Runners Without Docker

A runner using the LXC or host backend ignores `container:`. Install lgtmaybe
from PyPI in the job instead. The runner needs Python 3.11 or later:

```yaml
    runs-on: your-runner-label
    steps:
      - name: Review
        run: |
          python3 -m venv /tmp/lgtmaybe
          /tmp/lgtmaybe/bin/pip install --quiet 'lgtmaybe>=2,<3'
          /tmp/lgtmaybe/bin/python -m lgtmaybe action
        env:
          GITEA_TOKEN: ${{ secrets.LGTMAYBE_FORGEJO_TOKEN }}
          INPUT_PROVIDER: anthropic
          INPUT_MODEL: claude-sonnet-4-6
          INPUT_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

Keep the rest of the example as it is. The job still never checks out the pull
request: lgtmaybe reads the diff through the API.

## Try It Locally First

The repository has a throwaway Forgejo with a registered runner, for trying the
workflow before you touch a real instance. It needs Docker:

```bash
tests/forgejo/uat/up.sh --stub
```

It starts Forgejo at `http://localhost:3000` with two accounts and a runner
labelled `docker`. `--stub` adds a stand-in model, so a review runs with no API
key. Point the workflow at it with `INPUT_PROVIDER: openai-compatible`,
`INPUT_API_BASE: http://stub:8080/v1` and `INPUT_MODEL: mock`. Stop it with
`docker compose -f tests/forgejo/uat/compose.yml down -v`.

To see what a real model says about your branch, `lgtmaybe review` runs the
same review locally and prints the findings. See
[Review on Gitea](review-on-gitea.md#try-it-locally-first).

## What Works, and What Does Not

Mostly the same as on Gitea, because it is the same adapter. Every lens,
reflection, [redaction](../reference/glossary.md#redaction), static analysis, labels, the change overview, the
`fail_on` gate and slash commands work.
[Incremental review](../reference/glossary.md#incremental-review), 👎 feedback and symbol resolution do not.
The [Gitea page](review-on-gitea.md#what-works-and-what-does-not) explains why
for each one.

Resolve-on-fix never happens on Forgejo. Forgejo has no endpoint to resolve a
review comment, so fixed findings' comments stay open for a person to close.
This is the one place Forgejo differs from Gitea, which resolves them from 1.26.

Forgejo's web page can mark a review conversation resolved, but its API
cannot. That needs a signed-in browser session, so an API token is turned
away. No Forgejo release from 11 to 17 has a route for it. lgtmaybe's weekly
Forgejo checks watch for one, so resolve-on-fix can follow when it lands.

## Security

Forgejo's trigger rules mostly match Gitea's. `pull_request_target` takes the
workflow from the base branch, so a pull request cannot rewrite the review job.
It also gives that workflow the repository's secrets for pull requests from
forks. lgtmaybe never checks out or runs pull-request code, so that is safe to
do. Read
[Security: Who Can Change the Workflow](review-on-gitea.md#security-who-can-change-the-workflow)
for what this does not cover. The same limits apply on Forgejo.

Keep the trigger as `pull_request_target`. A `pull_request` run for a pull
request from a fork waits until a maintainer approves it. Even then it gets no
secrets, only a read-only job token, so lgtmaybe has neither the model key nor
a token that can post.

Slash commands work on pull requests from forks too, which they do not on
Gitea. An `issue_comment` run uses the workflow from the default branch and
gets the repository's secrets wherever the pull request came from.
lgtmaybe still runs a command only for a commenter with write access.

The example's `concurrency` block gives each comment that does not start with
`/` a group of its own. With a personal token, every comment lgtmaybe posts
starts a run of the workflow. Without that rule, the run would share the slash
command's group, and Forgejo would cancel the command that posted it. Keep that
line if you change the block.
