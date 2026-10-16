## 1. Spike: Prove the Write Path on Real Forgejo

- [x] 1.1 Commit `tests/forgejo/uat/compose.yml` (Forgejo plus a registered forgejo-runner with the `docker` label, reused by the user test and task 5.1), bring it up, create a repository and a pull request, and run `python -m lgtmaybe action` in the job container against it with a scoped `GITEA_TOKEN`; verify a review, a summary comment and a commit status post, and record any endpoint that fails in this change's design.md
- [x] 1.2 Repeat 1.1 with `GITEA_TOKEN: ${{ secrets.FORGEJO_TOKEN }}`; verify whether `GET /user`, the review post and a re-run's dedupe work, and settle design.md's first open question

## 2. Host Recognition

- [x] 2.1 Red first: add `tests/forgejo/test_actions_entrypoint.py` cases for `FORGEJO_SERVER_URL` winning over `GITHUB_SERVER_URL`, the `GITHUB_*`-only fallback, and no GHES refusal when `FORGEJO_SERVER_URL` is set; then change `cli._change_url` and verify the cases pass
- [x] 2.2 Name Forgejo beside Gitea in `parse_pr_url`'s unparseable-URL error and `GHES_UNSUPPORTED`; verify `tests/core/test_forge.py` asserts the new text
- [x] 2.3 Red first: a `tests/gitea/test_gateway.py` case where the permission call returns 403 and the logged warning names the token's missing permission; then reword the warning in `GiteaGateway.has_repo_write` and verify the case passes
- [x] 2.4 Add the `gitea.forgejo-host` rule to `openspec/specs/gitea-posting/anchors.yml` (bound to `_change_url`); verify `uv run pytest tests/specs -q`

## 3. Starter Workflow and Docs

- [x] 3.1 Red first: add `tests/forgejo/test_example_workflow.py`, mirroring the Gitea test (triggers, no `uses:`, only `python -m lgtmaybe action`, `LGTMAYBE_`-prefixed secret, rejecting `FORGEJO_`, `GITEA_` and `GITHUB_` prefixes, `runs-on: docker`, image major matching the package); then add `examples/forgejo/lgtmaybe.yml` and verify the test passes
- [x] 3.2 Bind `gitea.forgejo-workflow` to the example with a YAML rule in `anchors.yml`; verify `uv run pytest tests/specs -q`
- [x] 3.3 Write `docs/how-to/review-on-forgejo.md`, marked preview (setup, the Forgejo 11 floor, the runner label, the pip fallback for LXC and host runners, what works and what does not, the security section by link to the Gitea page); add it to `mkdocs.yml`, the README host table and links, `docs/index.md`, `docs/tutorial/getting-started.md` and `ARCHITECTURE.md`; verify `uv run mkdocs build --strict` and `uv run pytest tests/docs -q`
- [x] 3.4 Regenerate `docs/llms.txt` and `docs/llms-full.txt` with `uv run python docs/generate_llms_txt.py`; verify `tests/docs/test_llms_txt_fresh.py`

## 4. Workload Identity Federation

- [x] 4.1 Red first: add `tests/providers/test_federation.py` with a stubbed token endpoint (httpx `MockTransport`) covering the gate (no input, no request, environment unchanged), the missing-token error naming `enable-openid-connect: true`, a 403 leaving no token text in the message, an incomplete input set (Azure client ID without tenant ID, Bedrock without a region) failing before any request, and the `0600` file mode on POSIX; then add `providers/federation.py` and verify the tests pass on Ubuntu and Windows (explicit `encoding=` on every file write)
- [x] 4.2 Bedrock: assert the `sts.amazonaws.com` audience and the `AWS_ROLE_ARN`, `AWS_WEB_IDENTITY_TOKEN_FILE` and `AWS_REGION` variables; then check botocore resolves a web-identity provider from them with the STS call stubbed
- [x] 4.3 Azure: assert the `api://AzureADTokenExchange` audience and the three `AZURE_*` variables; then check `DefaultAzureCredential` selects workload identity from them
- [x] 4.4 Vertex: assert the `https://iam.googleapis.com/<provider>` audience and an `external_account` file with `credential_source.file`, plus impersonation when a service account is given; then check `google.auth.load_credentials_from_file` accepts it
- [x] 4.5 Wire federation into the `action` entrypoint before `_build_context_or_fail` reading the six inputs in the federation module (never `_RUNTIME_INPUTS`, see design decision 4); verify `tests/cli/test_action.py` (federation is called once, before provider build) and the `action.yml` parity tests still pass
- [x] 4.6 Pin the composite action gate: a test that `action.yml`'s `docker run` passes none of `INPUT_AWS_ROLE_ARN`, `INPUT_AZURE_CLIENT_ID`, `INPUT_GCP_WIF_PROVIDER`; verify it fails if one is added
- [x] 4.7 Create `openspec/specs/workload-identity-federation/anchors.yml` with one rule per requirement; verify `uv run pytest tests/specs -q`
- [x] 4.8 Add a keyless section per cloud to `review-on-forgejo.md` (issuer `https://<instance>/api/actions`, both `sub` formats, audiences, the public-JWKS requirement, the one-hour limit), add `examples/forgejo/lgtmaybe-bedrock.yml`, extend 3.1's test to it, and regenerate the llms files; verify `mkdocs build --strict` and `tests/docs`

## 5. Forgejo Contract Test

- [x] 5.1 Add `tests/forgejo/contract/` behind a `forgejo_contract` marker registered in `pyproject.toml` beside `e2e` and deselected by default, with a fixture that provisions a user, a token with the three scopes, a repository and a pull request through the API; verify `uv run pytest -m forgejo_contract` passes against a local `codeberg.org/forgejo/forgejo` container
- [x] 5.2 Cover `get_pr_context`, `post_review` with one inline finding and one demoted finding, the summary upsert on a re-run, dedupe, `create_check_run`, `has_repo_write` for a writer and a reader, and `apply_pr_labels`; verify all pass
- [x] 5.3 Add `.github/workflows/forgejo-contract.yml` (weekly, `workflow_dispatch`, and PRs touching `src/lgtmaybe/gitea/**` or `tests/forgejo/**`), pinned to a Forgejo major, every `uses:` pinned to a commit SHA; verify `tests/test_workflow_examples.py` and a green dispatch run

## 6. Integration

- [x] 6.1 Run `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv lock --check` and `npx -y @fission-ai/openspec@latest validate --specs`; verify all pass
- [ ] 6.2 Hand `user-test.md` to the tester and record results in it; verify every scenario marked "must pass" passes, and fix or file anything that does not

## Workflow follow-up

- The delta specs are already folded into `openspec/specs/` (each implementation PR synced its own requirements, with anchors). Once the user test passes, archive with `openspec archive forgejo-actions-support --skip-specs` so they are not applied twice.
- Drop "preview" from the docs after two green scheduled contract runs.
