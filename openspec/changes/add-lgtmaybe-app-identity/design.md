## Context

lgtmaybe is correctly implemented as a GitHub Action: the runner executes the
review, repository secrets provide model credentials, and `GITHUB_TOKEN`
provides repository access. The unavoidable consequence is that GitHub
attributes every API write to `github-actions[bot]`.

A public GitHub App named `lgtmaybe` already exists (App ID `3987976`) with no
webhook subscriptions. The current working tree also contains an advanced
`app_id`/`app_private_key` Action path and a dogfood workflow prepared to use
it, but the App is not yet represented by repository variables or secrets.
That path is suitable for the maintainer's own repository; distributing the
App private key to users is not.

## Goals / Non-Goals

**Goals:**

- Attribute opted-in reviews and PR comments to `lgtmaybe[bot]`.
- Keep review execution and all provider credentials inside GitHub Actions.
- Give users a short installation flow with no App-development knowledge or
  private-key handling.
- Restrict every minted token to the requesting installed repository and the
  minimum permissions lgtmaybe uses.
- Make the identity boundary observable, revocable, and safe to dogfood.

**Non-Goals:**

- Turn lgtmaybe into a hosted review service.
- Store provider keys, diffs, findings, repository contents, or user config.
- Replace the zero-hosting `github-actions[bot]` path.
- Add billing, persistent user accounts, or a GitHub App settings UI.
- Trigger reviews from App webhooks; repository workflows remain the trigger.

## Decisions

1. **Keep a hybrid Action plus App architecture.** The Action remains the
   product runtime; the App supplies only a branded GitHub identity. A fully
   hosted App would make lgtmaybe responsible for customer model credentials
   and compute, while an Action alone cannot change its API actor.

2. **Exchange GitHub Actions OIDC for an installation token.** In branded mode
   the Action requests a JWT with a fixed lgtmaybe audience and sends it to a
   small HTTPS broker. The broker validates GitHub's issuer and JWKS signature,
   audience, time claims, immutable `repository_id`/`repository_owner_id`,
   repository name, default-branch workflow ref, and an allowlisted base-safe
   event (`pull_request_target`, `issue_comment`, or
   `pull_request_review_comment`). It then confirms that the App is installed
   on that exact repository.

3. **Mint the narrowest token GitHub permits.** The broker authenticates as the
   lgtmaybe App, requests an installation token limited to the verified
   repository ID, and narrows permissions to `contents: read`,
   `pull_requests: write`, and `issues: write`. It never returns an
   installation-wide token. The App registration drops its current
   `contents: write` grant to `contents: read`. The public App deliberately
   excludes `checks: write`; public branded mode rejects `fail_on` with setup
   guidance instead of gaining permission to forge a required merge check.

4. **Host the broker as a small Python AWS Lambda behind API Gateway.** The App
   private key lives in AWS Secrets Manager and only the Lambda role can read
   it. API Gateway supplies TLS and throttling; Lambda has explicit short
   timeouts for GitHub JWKS and API calls. CDK Python provisions the isolated
   stack. No database is required because GitHub is the installation source of
   truth and both OIDC and installation tokens are short-lived.

5. **Make identity selection explicit.** A `github_identity` Action input uses
   `actions` by default and `lgtmaybe` for the broker path. Existing
   `app_id`/`app_private_key` inputs remain an advanced self-managed option for
   organisations that want their own App identity. Conflicting identity inputs
   fail validation instead of choosing implicitly.

6. **Never silently lose the requested identity.** `github_identity:
   lgtmaybe` fails with an install link or a broker-specific diagnostic when
   the App is missing or exchange fails. It does not fall back to
   `github-actions[bot]`. The default `actions` mode never contacts the broker.

7. **Keep tokens internal and short-lived.** The exchange response is masked,
   passed only to the lgtmaybe container, never exposed as a public Action
   output, and revoked in an `always()` cleanup step when possible. Natural
   expiry remains the cancellation fallback. Neither raw OIDC JWTs nor
   installation tokens are logged.

8. **Teach the architecture through a two-path setup.** Marketplace, README,
   and how-to content first explain that the Action does the work. Users then
   choose either zero-install setup (`github-actions[bot]`) or install the
   public App plus add `id-token: write` (`lgtmaybe[bot]`). Provider/model
   configuration stays identical in both examples. Self-managed App creation
   moves to an advanced section.

9. **Dogfood before public rollout.** Install the existing App only on
   `MattJColes/lgtmaybe`, temporarily store its ID/private key for the already
   prepared direct path, and verify real review/reply/resolve/label behavior.
   After broker deployment, switch the dogfood workflow to OIDC, verify the
   actor again, and delete the repository-held private key.

## Risks / Trade-offs

- [The broker becomes an availability dependency for branded mode] -> Keep
  Actions mode independent, retry only idempotent GitHub reads, and fail with a
  clear status rather than retry token creation or post under the wrong actor.
- [A broker compromise could mint App tokens] -> Store the key in Secrets
  Manager, use a least-privilege Lambda role, validate immutable repository
  claims, scope every token down, throttle requests, and support rapid key
  rotation/revocation.
- [An unsafe caller workflow could expose its token] -> Accept only base-safe
  events/default-branch workflow refs, document `pull_request_target` plus
  API-only diff access, mask the token, and revoke it after use.
- [App permission changes can surprise existing installations] -> Reduce
  permissions before public onboarding and show the exact grants in the guide.
- [The existing working tree has overlapping unfinished App documentation] ->
  implement against the resolved final files, preserve unrelated work, and use
  the new acceptance tests to settle the intended copy.

## Migration Plan

1. Add failing broker, Action-input, workflow, and documentation acceptance
   tests.
2. Implement and deploy the broker to a non-public endpoint; configure the App
   private key in Secrets Manager.
3. Reduce the public App permissions, install it on `MattJColes/lgtmaybe`, and
   exercise the existing direct private-key dogfood path.
4. Switch the dogfood workflow to `github_identity: lgtmaybe` with
   `id-token: write`; verify GitHub attributes real review operations to
   `lgtmaybe[bot]`.
5. Publish the install link and two-path setup, then remove the App private key
   from the repository.
6. Roll back by changing the dogfood workflow to `github_identity: actions`;
   the Action-only product remains functional even if the broker is disabled.
