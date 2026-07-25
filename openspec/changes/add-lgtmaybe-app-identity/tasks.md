## 1. Acceptance Tests

- [x] 1.1 Add broker tests for valid exchange, invalid signature/issuer/audience/time claims, mutable-name mismatch, disallowed event, non-default workflow ref, missing installation, repository scoping, permission scoping, and secret-free logs; run them and confirm they fail.
- [x] 1.2 Add Action metadata tests for the default Actions identity, explicit lgtmaybe identity, conflicting self-managed credentials, masked internal token handling, cleanup, and actionable failures; run them and confirm they fail.
- [x] 1.3 Add documentation tests requiring the two setup paths, public App install link, `id-token: write`, unchanged provider inputs, permissions explanation, and no private key in the public branded example; run them and confirm they fail.

## 2. Identity Broker

- [x] 2.1 Add the isolated Python broker package with strict request/response models and explicit timeouts for every GitHub/JWKS call.
- [x] 2.2 Implement GitHub OIDC verification for signature, issuer, fixed audience, time, immutable repository identity, repository name, default-branch workflow ref, and the allowlisted base-safe events.
- [x] 2.3 Implement App installation lookup and mint a token restricted to the verified repository ID with only contents-read, pull-requests-write, and issues-write permissions.
- [x] 2.4 Ensure responses and structured logs never contain raw OIDC JWTs, installation tokens, repository contents, or provider data; retry only safe GitHub reads and never retry token creation.
- [x] 2.5 Add a Python CDK stack for API Gateway, throttled Lambda execution, Secrets Manager access, least-privilege IAM, alarms, and configurable App ID/private-key secret; add CDK assertion tests.
- [ ] 2.6 Run the broker unit/integration tests and CDK synthesis/assertion tests, then deploy a maintainer-only endpoint and smoke-test valid and rejected exchanges.

## 3. GitHub Action Identity

- [x] 3.1 Add and validate the `github_identity` input (`actions` default, `lgtmaybe` broker mode) while retaining the existing App ID/private-key path as advanced self-managed authentication.
- [x] 3.2 In lgtmaybe mode, request a GitHub OIDC token for the fixed audience, exchange it through the broker with explicit timeouts and no retry of token creation, mask the returned token, and pass it only to the existing container entrypoint.
- [x] 3.3 Add an `always()` cleanup step that revokes the brokered installation token when the job reaches cleanup, without exposing it as an Action output.
- [x] 3.4 Emit distinct actionable failures for missing `id-token: write`, missing App installation, invalid workflow provenance, broker unavailability, and conflicting identity inputs; never fall back after lgtmaybe mode is selected.
- [x] 3.5 Run the focused Action, CLI entrypoint, workflow example, packaging, and security tests.

## 4. App Registration and Dogfooding

- [x] 4.1 Change the existing public App registration from contents-write to contents-read while retaining pull-requests-write, issues-write, metadata-read, and no webhook subscriptions.
- [x] 4.2 Install the public App only on `MattJColes/lgtmaybe`, generate a temporary private key, set `LGTMAYBE_APP_ID=3987976`, and store the key as `LGTMAYBE_APP_PRIVATE_KEY` without writing it to the workspace or logs.
- [x] 4.3 Run a real dogfood review through the existing private-key path and verify review comments, summaries, replies, resolutions, labels, descriptions, and diagrams use `lgtmaybe[bot]`.
- [x] 4.4 Store the App private key in the broker's Secrets Manager secret, deploy the production broker, and switch the dogfood workflow to `github_identity: lgtmaybe` with `id-token: write`.
- [ ] 4.5 Run a real brokered dogfood review, verify attribution and token cleanup, then delete `LGTMAYBE_APP_PRIVATE_KEY` and the no-longer-needed App ID variable from the repository.

## 5. User Onboarding

- [x] 5.1 Update `action.yml` Marketplace descriptions, README, and the primary GitHub Action guide to explain what the Action does, what the App does, and when users see `github-actions[bot]` versus `lgtmaybe[bot]`.
- [x] 5.2 Rewrite the App how-to as a short public install flow: install on selected repositories, grant `id-token: write`, set `github_identity: lgtmaybe`, and keep provider/model/authentication inputs unchanged.
- [x] 5.3 Update the App profile/homepage and add complete default, public branded, and advanced self-managed workflow examples with the exact permissions each path needs.
- [x] 5.4 Document the identity broker's data boundary, availability behavior, token lifetime/scoping, App permissions, uninstall/revocation path, and troubleshooting messages.
- [x] 5.5 Regenerate derived reference and `llms.txt` documentation, then run the documentation tests and strict MkDocs build.

## 6. Specification and Release Verification

- [x] 6.1 Add or update living-spec sections and ast-grep anchors for the implemented broker and Action identity boundaries; ensure every anchor resolves exactly once.
- [x] 6.2 Run the full relevant test suite, formatters, linters, type checks, security checks, `uv run pytest tests/specs -q`, and OpenSpec validation.
- [x] 6.3 Verify rollback by switching a test workflow to Actions identity and confirming it neither calls the broker nor changes existing review behavior.
- [ ] 6.4 Publish the Action change, repeat the install guide from a clean test repository, and confirm a maintainer can reach `lgtmaybe[bot]` without receiving or creating an App private key.
