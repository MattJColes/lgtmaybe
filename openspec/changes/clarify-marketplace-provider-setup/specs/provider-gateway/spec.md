## MODIFIED Requirements

### Requirement: One flag builds the whole client
<!-- anchor: provider.factory -->

`build_provider` SHALL map `(provider, model)` plus optional key/base/fallback
to a configured client - provider strategy selection lives here, not in the
engine. All model slots (triage, review, reflect) share one provider and one
set of credentials. The GitHub Action setup SHALL show that Marketplace users
select the provider, model, and matching authentication inputs in workflow
configuration.

#### Scenario: user picks a provider
- **WHEN** `--provider bedrock` is given
- **THEN** the factory returns a client whose calls route via litellm's
  bedrock path with ambient AWS credentials

#### Scenario: Marketplace user configures the Action
- **WHEN** a user adopts lgtmaybe from GitHub Marketplace
- **THEN** the setup guidance shows a workflow `with:` block containing a
  provider, model, and matching authentication input
