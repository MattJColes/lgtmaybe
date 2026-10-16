## Context

See proposal.md for why. The facts the design rests on, checked during the
investigation:

- Codeberg (Forgejo 16, reporting `gitea-1.22.0` API compatibility) serves
  every read endpoint `GiteaGateway` calls, in the shape it expects: PR
  metadata (`base.sha`, `head.sha`, `head.ref`), `pulls/{n}.diff`, `/files`,
  `/commits`, `/reviews` and `/labels`.
- The spike (task group 1) ran the released lgtmaybe v2 image as a Forgejo
  Actions job on Forgejo 17 with forgejo-runner 12, the stub model standing in
  for a provider. With a personal token: inline review, summary upsert, re-run
  dedupe, commit status, `/describe` from the maintainer, an outsider's `/ask`
  ignored with no reply, and a fork pull request reviewed through
  `pull_request_target`. With the automatic job token: reviews post as
  `forgejo-actions` and re-runs de-duplicate, but the collaborator-permission
  call returns 403, so every slash command is ignored (fail closed).
- A follow-up check on Forgejo 17 with runner v12: every `GITHUB_*` variable
  arrives with a `FORGEJO_*` twin of the same value, so the entrypoint reads
  either name for the event too. A fork's `pull_request` run waits for a
  maintainer's approval and gets no secrets; `pull_request_target` and an
  `issue_comment` on a fork's pull request both get the secrets at once. A
  comment posted with a personal token starts a workflow run, which shared the
  slash command's concurrency group and cancelled it until the examples gave
  non-command comments their own group. No Forgejo from 11 to 17 has a REST
  route to resolve a review conversation (the web route needs a browser
  session), so resolve-on-fix stays unclaimed, with a contract test watching.
- Forgejo refuses secret names starting with `FORGEJO_`, as well as `GITEA_`
  and `GITHUB_` (HTTP 400).
- forgejo-runner v7 and later sets each `FORGEJO_*` variable and its `GITHUB_*`
  alias. Earlier runners set only `GITHUB_*`. `GITHUB_API_URL` ends in
  `/api/v1`.
- Forgejo runners have no default labels. Admins choose them; `docker` is the
  usual Docker-backed label. `container:` is honoured on Docker and Podman
  runners and ignored on LXC and host runners.
- `uses:` without a full URL resolves against `data.forgejo.org`, and the
  composite action needs a Docker CLI inside the job, so it is not a Forgejo
  path.
- Forgejo Actions mints OIDC ID tokens when the workflow or job sets
  `enable-openid-connect: true`. The issuer is `https://<instance>/api/actions`.
  The subject is `repo:<repository>:ref:<ref>`, where `<repository>` is
  `owner/repo` for repositories that enabled Actions before Forgejo 16 and
  `owner-ID/repo-ID` after. It is disabled for fork `pull_request` runs, not for
  `pull_request_target`.
- `FORGEJO_TOKEN` is the automatic job token and is set on every job.

## Goals / Non-Goals

**Goals:**

- A Forgejo user copies one file, adds two secrets, sets one runner label, and
  gets a review.
- Keyless Bedrock, Azure and Vertex on Forgejo, with no change to the GitHub
  path.
- Catch Forgejo API divergence in our CI, not in a user's.

**Non-Goals:**

- A `Forge.forgejo` value or a Forgejo gateway. Same API, same URL shape: a
  second adapter would be a copy with no variant to justify it.
- Incremental review, resolve-on-fix, 👎 feedback and symbol resolution on
  Forgejo. They are unbuilt on Gitea for API reasons that Forgejo shares.
- Woodpecker CI (Codeberg's other CI). It has no Actions event payload; that is
  a separate entrypoint, as `gitlab-ci` is.
- Federation for `gitlab-ci` and `azure-pipelines`. GitLab's `id_tokens:` hands
  the JWT over as a variable, so it would be a different gate; a follow-up can
  reuse the writer.

## Decisions

**1. Forgejo rides the Gitea adapter.** `parse_pr_url` already routes
`/pulls/{n}` to `Forge.gitea`, and the token variable stays `GITEA_TOKEN`.
User-facing text says "Gitea or Forgejo". Alternative considered: an alias
`Forge.forgejo` mapping to the same builder. Rejected because nothing in the
code would branch on it, and AGENTS.md bars a new abstraction without a second
variant. Revisit when the API diverges; the contract test (decision 6) is what
tells us.

**2. `FORGEJO_SERVER_URL` wins in `_change_url`.** Read it first, fall back to
`GITHUB_SERVER_URL`. When it is set, skip the GitHub Enterprise Server check.
A Forgejo instance cannot be GHES, and this keeps the check from tripping if
Forgejo ever changes `GITHUB_API_URL`. Alternative: keep reading only
`GITHUB_SERVER_URL`. It works today but relies on an alias Forgejo describes as
compatibility.

**3. Do not read `FORGEJO_TOKEN` as a forge token.** It is set on every job. If
the token chain read it, a missing secret would silently fall back to the job
token. If that identity cannot answer `GET /user`, `_posting_login` returns
None, dedupe trusts nothing, and every re-run posts duplicate findings. The
workflow maps `GITEA_TOKEN` explicitly, from `LGTMAYBE_FORGEJO_TOKEN`. The spike
showed the job token reviews correctly but cannot read a commenter's
permission, so the docs offer `GITEA_TOKEN: ${{ secrets.FORGEJO_TOKEN }}` as a
reviews-only opt-in, naming the trade-off. The gateway's 403 log line names the
cause (the token cannot read collaborator permissions) instead of only "no
write access", so a user who opted in can see why commands are ignored.

**4. Federation lives in Python, in the container.** A new
`providers/federation.py` exposes one function, called from the `action`
entrypoint before `_build_context_or_fail`. It reads the three inputs, fetches
the ID token over httpx with the cloud's audience, writes it to a `0600` file in
a fresh `tempfile.mkdtemp()` directory, and sets the SDK-native variables in
`os.environ`. `resolve_credentials` is untouched: botocore,
`DefaultAzureCredential` and google-auth find the federated credential as
ambient credentials.

Alternatives considered:

- Shell steps in the example workflow, as `action.yml` does for Azure. Rejected:
  three clouds of curl and JSON in user-copied YAML is the opposite of out of
  the box, and it cannot be unit-tested.
- Federation inside `resolve_credentials`. Rejected: that is the provider chain
  for every surface, including the local CLI. The runner token is a property of
  the CI entrypoint, so it is wired there.

Input names match `action.yml` (`aws_role_arn`, `aws_region`,
`gcp_wif_provider`, `gcp_service_account`, `azure_client_id`,
`azure_tenant_id`). The federation module reads them from the environment
itself; they do not join `_RUNTIME_INPUTS`, because the `action.yml` parity
tests would then require them in the composite's container `env:` block, which
is exactly what must never happen. A test pins that the composite never
forwards them, so the gate in the spec holds on GitHub by construction.

The spike confirmed the shape against Forgejo 17's real token endpoint: the
response carries the token as `value`, the runner's query (`api-version`)
survives the merged `audience` parameter, and `pull_request_target` and
`issue_comment` runs both get `repo:<repository>:ref:refs/heads/<default>`.

**5. Audiences follow each cloud's own default.** `sts.amazonaws.com` for AWS,
`api://AzureADTokenExchange` for Entra, and
`https://iam.googleapis.com/<provider>` for GCP. The trust policy the user
writes then matches the cloud's own docs. Forgejo's default audience
(`<instance>/<owner>`) is not used, because no cloud expects it.

**6. A Forgejo contract test, scheduled, not per-PR.** A
`forgejo-contract.yml` workflow starts the `codeberg.org/forgejo/forgejo` image
as a service. It creates a user, a token with the three scopes, a repository
and a pull request through the API. Then it runs `tests/forgejo/contract/`
against it: `get_pr_context`, `post_review`, the summary upsert, dedupe on
re-run, `create_check_run`, `has_repo_write` and `apply_pr_labels`. No model
call. It runs weekly, on manual dispatch, and on PRs touching
`src/lgtmaybe/gitea/**` or `tests/forgejo/**`. It is kept out of the default
pytest run with a marker, because it needs Docker and a network.

**7. The example uses `runs-on: docker`.** It is the label Forgejo's runner
docs use. The comment says the label belongs to the instance admin, and gives
the pip fallback for LXC and host runners: `pip install
'lgtmaybe[bedrock,azure,vertex]'`, then `python -m lgtmaybe action`.

## Risks / Trade-offs

- [A Forgejo write endpoint differs from Gitea's] → The contract test catches it
  before release. Until it is green, docs call Forgejo support "preview".
- [The ID token expires before a long review finishes] → botocore and
  azure-identity re-read the file only to refresh, and the file is never
  refreshed. STS and Entra credentials last an hour, the default
  `max_review_seconds` is 3600, and a refresh past the JWT's lifetime fails
  loudly, not silently. Document it. A refresh hook that re-requests the token
  is a follow-up if the user test hits it.
- [The cloud cannot reach a private instance's signing keys] → Unavoidable:
  AWS, Entra and GCP all fetch the issuer's JWKS. The docs say so up front and
  point private instances at an API key or ollama.
- [The `sub` format changed in Forgejo 16] → The docs give both forms and tell
  users to read the claim from a test token, not guess it.
- [`runs-on: docker` is wrong on some instances] → It is a one-word edit, named
  in the first line of the example's comment.
- [Codeberg's shared runners may not honour `container:`] → Unverified. The
  user test covers it; if they do not, the docs lead with the pip fallback for
  Codeberg.

## Migration Plan

Additive. No existing run changes: Gitea and GitHub workflows set neither
`FORGEJO_SERVER_URL` nor any federation input inside the container. Rollback is
a revert.

## Open Questions

- Do Codeberg's shared runners honour job `container:`? It changes only which
  setup path the docs lead with.
