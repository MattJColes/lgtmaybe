## Context

lgtmaybe is published to GitHub Marketplace as an Action. GitHub's Marketplace flow can copy a `uses:` step, but it does not render an interactive form for Action inputs. Users must therefore choose the provider, model, and authentication method in workflow YAML. The runtime already supports this correctly, while the onboarding path leaves the configuration step implicit.

## Goals / Non-Goals

**Goals:**

- Explain the Marketplace constraint at the first useful setup point.
- Give users one complete, minimal hosted-provider example.
- Route other provider choices to the existing copy-paste workflows.
- Keep the guidance checked by the existing documentation test suite.

**Non-Goals:**

- Build a hosted configuration service or GitHub App settings UI.
- Choose a provider or model on the user's behalf.
- Change runtime provider defaults, authentication, or permissions.
- Duplicate every provider guide in the Marketplace introduction.

## Decisions

1. Treat the workflow file as the configuration surface. This matches GitHub Actions and the existing `provider`, `model`, and authentication inputs. A separate settings store would add infrastructure without fixing the Marketplace limitation.
2. Lead with one complete OpenAI example and link to the existing provider-specific workflows. A single working path is easier to scan, while the examples already encode the different keyless and key-based authentication shapes.
3. Clarify the setup through `action.yml`, the README's GitHub Action section, and the focused how-to guide. These are the surfaces a Marketplace visitor and a repository adopter encounter.
4. Add a small documentation acceptance test that asserts the quick-start example includes `provider`, `model`, and `api_key`. This protects the essential handoff without testing prose formatting.

## Risks / Trade-offs

- [GitHub may change the Marketplace installation UI] -> Phrase the guidance around the durable workflow configuration contract and keep the UI claim short.
- [The example may appear to endorse one provider] -> Label OpenAI as an example and place the full provider workflow link beside it.
- [Model identifiers become stale] -> Reuse the repository's current example and existing provider guides rather than introducing a second model catalogue.
