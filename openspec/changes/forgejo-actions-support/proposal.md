## Why

Forgejo split from Gitea and is now the forge behind Codeberg and a growing
share of self-hosted instances. Its REST API is still Gitea's `/api/v1`, and
forgejo-runner comes from the same `act` lineage as Gitea's runner. So our Gitea
adapter already reviews a Forgejo pull request. What does not work out of the
box is everything around it. There is no Forgejo workflow, and the Gitea example
asks for a runner label (`ubuntu-latest`) that Forgejo runners do not have by
default. Nothing tells a Forgejo user that the Gitea path is theirs. And keyless
cloud auth, the wedge, stops at GitHub, even though Forgejo Actions can now mint
OIDC ID tokens the same way GitHub does.

## What Changes

- Forgejo is a supported host through the existing Gitea adapter. No new
  `Forge` value and no new gateway: same API, same `/pulls/{n}` URL shape.
- The `action` entrypoint reads `FORGEJO_SERVER_URL` (forgejo-runner v7 and
  later) ahead of `GITHUB_SERVER_URL`, so it keeps working if Forgejo ever drops
  the `GITHUB_*` aliases. A run that sets it is never mistaken for GitHub
  Enterprise Server.
- New `examples/forgejo/lgtmaybe.yml`: `pull_request_target` plus
  `issue_comment`, `runs-on: docker`, the lgtmaybe image as the job container,
  and the token mapped explicitly from a `LGTMAYBE_`-prefixed secret.
- Keyless Bedrock, Azure and Vertex from any runner that exposes a workflow
  OIDC token (`ACTIONS_ID_TOKEN_REQUEST_URL` and `_TOKEN`). The `action`
  entrypoint fetches the token with the right audience, writes it to a private
  temporary file, and sets the variables each cloud SDK already reads. The
  inputs (`INPUT_AWS_ROLE_ARN`, `INPUT_AZURE_CLIENT_ID`,
  `INPUT_GCP_WIF_PROVIDER` and their companions) reuse the Action's names. The
  GitHub composite action is unchanged, because those inputs never reach its
  container.
- Host-naming text (the unparseable-URL error, the GitHub Enterprise Server
  refusal) names Forgejo beside Gitea.
- Docs: `docs/how-to/review-on-forgejo.md`, a pip-install fallback for LXC and
  host runners, a keyless section per cloud, and the README and explanation
  host tables.
- A scheduled contract test that runs the Gitea adapter's write calls against a
  real Forgejo container, so an API divergence shows up before a user finds it.
- A user acceptance test plan (`user-test.md`) for a tester on a real instance.

## Capabilities

### New Capabilities

- `workload-identity-federation`: how a CI run's OIDC ID token becomes keyless
  Bedrock, Azure and Vertex credentials inside the lgtmaybe container, when it
  is attempted, and how it fails.

### Modified Capabilities

- `gitea-posting`: the adapter serves Forgejo as well as Gitea. Adds how a
  Forgejo run is recognised and what the Forgejo starter workflow guarantees.

## Impact

- Code: `cli/__init__.py` (`_change_url`, the entrypoint calling federation),
  `core/forge.py` (message text only), new
  `src/lgtmaybe/providers/federation.py`.
- No new dependency: botocore, azure-identity and google-auth already ship in
  the image's `bedrock`, `azure` and `vertex` extras, and HTTP uses httpx.
- New `examples/forgejo/`, `docs/how-to/review-on-forgejo.md`,
  `tests/forgejo/`, and `.github/workflows/forgejo-contract.yml`.
- Unchanged: the `ReviewGateway` port, the engine, the GitHub composite action,
  and the Gitea example.
- Not built, and not claimed for Forgejo: incremental review, resolve-on-fix,
  👎 feedback and symbol resolution, all for the same reasons as on Gitea.
