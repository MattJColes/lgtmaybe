## Why

lgtmaybe reviews currently post as `github-actions[bot]`, which hides the
product identity and makes the public `lgtmaybe` GitHub App appear inert.
Users need a clear, safe installation path that keeps model credentials in
their workflow while attributing review activity to `lgtmaybe[bot]`.

## What Changes

- Keep the GitHub Action as the review runtime and workflow `with:` block as
  the provider, model, and provider-authentication surface.
- Make the public `lgtmaybe` GitHub App a thin identity and permissions layer:
  users install it on selected repositories, then opt into branded posting in
  their workflow.
- Add a small hosted token broker that validates a GitHub Actions OIDC token,
  confirms the matching App installation, and returns a short-lived,
  repository-scoped installation token. The broker never receives diffs,
  findings, or model credentials.
- Update the Action to request and use the installation token when branded
  posting is selected, while preserving `github-actions[bot]` as the
  zero-hosting default.
- Replace the current App-development guidance with a two-path onboarding
  choice: basic Action setup or Action plus the installed lgtmaybe App.
- Dogfood the branded path in `MattJColes/lgtmaybe`, first through the existing
  private-key flow and then through the public broker flow.
- Tighten the existing App registration to the minimum repository permissions
  required by lgtmaybe.

## Capabilities

### New Capabilities

- `github-app-identity`: Installation, OIDC exchange, token brokering,
  least-privilege App permissions, failure behavior, and user onboarding for
  posting as `lgtmaybe[bot]`.

### Modified Capabilities

- `github-posting`: Reviews and related PR activity can be attributed to the
  installed lgtmaybe App instead of the built-in GitHub Actions App.
- `cli-and-local`: The GitHub Action entrypoint gains an explicit identity mode
  and obtains App credentials without exposing the App private key to users.

## Impact

The change affects `action.yml`, the dogfood and example workflows, GitHub
authentication wiring, Marketplace/README/how-to documentation, and the public
GitHub App registration. It introduces a small hosted security boundary for
OIDC validation and installation-token minting plus its deployment and
operational configuration. Review execution, provider routing, model
credentials, local CLI behavior, and diff handling remain unchanged.
