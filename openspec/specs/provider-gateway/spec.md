# provider-gateway Specification

## Purpose

The LLM adapter track: one litellm-backed client behind the `ProviderClient`
port, a factory that builds it from the `--provider` flag, and credential
resolution as a chain of responsibility — the wedge being keyless OIDC/WIF for
Bedrock/Vertex/Azure with no static cloud keys, ever.

## Requirements

### Requirement: One flag builds the whole client

`build_provider` SHALL map `(provider, model)` plus optional key/base/fallback
to a configured client — provider strategy selection lives here, not in the
engine. All model slots (triage, review, reflect) share one provider and one
set of credentials.
<!-- anchor: provider.factory -->

#### Scenario: user picks a provider
- **WHEN** `--provider bedrock` is given
- **THEN** the factory returns a client whose calls route via litellm's
  bedrock path with ambient AWS credentials

### Requirement: Credentials resolve by chain, fail with instructions

Resolution SHALL try the chosen provider's native mode first (ambient cloud
creds for bedrock/vertex/azure), then an API key from flag/env; ollama needs
neither; openai-compatible needs an `api_base` with the key optional
(placeholder when absent). Static cloud keys (AWS keys, service-account JSON)
are never accepted or required; exhaustion fails with a clear
"how to auth this provider" message.
<!-- anchor: provider.credentials -->

#### Scenario: nothing resolves
- **WHEN** a provider has neither ambient creds nor a key
- **THEN** the run fails naming exactly how to authenticate that provider

### Requirement: Completion calls retry, fall back, and cache

`LiteLLMProvider.complete` SHALL retry transient failures, switch to
`fallback_model` when the primary is exhausted, and — on routes with explicit
cache breakpoints — place them on the shared prefix so lens calls 2..N read
the preamble-plus-diff from cache. Cache read/creation token counts land on
`ProviderResult`.
<!-- anchor: provider.complete -->

#### Scenario: primary model keeps failing
- **WHEN** retries on the primary model are exhausted and a fallback is set
- **THEN** the call completes on the fallback model instead of failing the review

### Requirement: Defaults are provider-aware

Timeouts SHALL default long for providers that may front a slow model —
local-capable ones (ollama/openai-compatible) and openrouter, a gateway to
arbitrary models including slow reasoning ones — and short for direct cloud
providers; the litellm model string is derived per provider so users give bare
model ids.
<!-- anchor: provider.defaults -->

#### Scenario: no timeout configured
- **WHEN** `timeout` is unset and the provider is ollama
- **THEN** the generous (long) default applies, not the cloud one

#### Scenario: openrouter gets the generous default
- **WHEN** `timeout` is unset and the provider is openrouter
- **THEN** the generous (long) default applies, not the cloud one
