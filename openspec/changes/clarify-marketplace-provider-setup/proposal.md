## Why

GitHub Marketplace can add the lgtmaybe Action to a workflow, but it cannot present an interactive provider or model picker. The current Marketplace-facing path does not explain that users must configure those choices in the generated workflow, so a minimal install leaves the essential next step unclear.

## What Changes

- Make the Marketplace-facing Action metadata state where provider and model selection happens.
- Add a short Marketplace setup path that shows the minimum working `with:` block and credential secret.
- Link directly to the provider-specific workflow examples for users who need Bedrock, Vertex, Azure, OpenRouter, z.ai, or an OpenAI-compatible endpoint.
- Remove the separate GitHub App setup from the default journey and make the old URL clarify that the Marketplace listing is an Action.
- Add a documentation acceptance check so the Marketplace setup cannot regress to an unconfigured `uses:` example.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `provider-gateway`: Clarify the user-facing requirement that Action users select the provider, model, and matching authentication inputs in workflow configuration.

## Impact

The change is limited to Marketplace-facing Action metadata, setup documentation, navigation, and documentation tests. It does not change provider routing, authentication behavior, runtime defaults, dependencies, or the GitHub permissions model.
