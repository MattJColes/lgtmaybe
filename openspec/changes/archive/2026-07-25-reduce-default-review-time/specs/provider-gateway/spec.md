## MODIFIED Requirements

### Requirement: Completion calls retry, fall back, cache, and time out
<!-- anchor: provider.complete -->

`LiteLLMProvider.complete` SHALL enforce the configured request timeout at the
adapter boundary, retry transient failures within its bounded budget, switch to
`fallback_model` when the primary is exhausted, and place explicit cache
breakpoints on the shared prefix for supported routes. Cache usage SHALL land
on `ProviderResult`.

#### Scenario: provider SDK ignores its timeout
- **WHEN** the underlying completion call remains blocked past the configured
  timeout
- **THEN** the adapter raises a timeout error and the existing bounded retry
  policy decides whether to retry

#### Scenario: primary model keeps failing
- **WHEN** retries on the primary model are exhausted and a fallback is set
- **THEN** the call completes on the fallback model instead of failing the
  review
