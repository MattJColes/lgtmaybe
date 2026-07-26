# provider-gateway Specification

## Purpose

The LLM adapter track: one litellm-backed client behind the `ProviderClient`
port, a factory that builds it from the `--provider` flag, and credential
resolution as a chain of responsibility — the wedge being keyless OIDC/WIF for
Bedrock/Vertex/Azure with no static cloud keys, ever.
## Requirements
### Requirement: One flag builds the whole client

`build_provider` SHALL map `(provider, model)` plus optional key/base/fallback
to a configured client - provider strategy selection lives here, not in the
engine. All model slots (triage, review, reflect) share one provider and one
set of credentials. The GitHub Action setup SHALL show that Marketplace users
select the provider, model, and matching authentication inputs in workflow
configuration. It SHALL also state that the Action uses GitHub Actions' built-in
token and does not require a separate GitHub App.
<!-- anchor: provider.factory -->

#### Scenario: user picks a provider
- **WHEN** `--provider bedrock` is given
- **THEN** the factory returns a client whose calls route via litellm's
  bedrock path with ambient AWS credentials

#### Scenario: Marketplace user configures the Action
- **WHEN** a user adopts lgtmaybe from GitHub Marketplace
- **THEN** the setup guidance shows a workflow `with:` block containing a
  provider, model, and matching authentication input

#### Scenario: Marketplace user authenticates to GitHub
- **WHEN** a user runs lgtmaybe as a GitHub Action
- **THEN** the setup guidance says no separate GitHub App installation is required

### Requirement: Credentials resolve by chain, fail with instructions

Resolution SHALL try the chosen provider's native mode first (ambient cloud
creds for bedrock/vertex/azure), then an API key from flag/env; ollama needs
neither; openai-compatible needs an `api_base` with the key optional
(placeholder when absent). Vertex ambient credential probing MUST prefer
`CLOUDSDK_CONFIG`, then use `%APPDATA%\gcloud` on Windows or
`~/.config/gcloud` on POSIX. Static cloud keys (AWS keys, service-account JSON)
are never accepted or required; exhaustion fails with a clear "how to auth
this provider" message.
<!-- anchor: provider.credentials -->

#### Scenario: nothing resolves
- **WHEN** a provider has neither ambient creds nor a key
- **THEN** the run fails naming exactly how to authenticate that provider

#### Scenario: Vertex uses the default Windows gcloud location
- **WHEN** a Windows user has ADC under `%APPDATA%\gcloud` and
  `CLOUDSDK_CONFIG` is unset
- **THEN** ambient Vertex authentication is detected

#### Scenario: Vertex uses an explicit gcloud location
- **WHEN** `CLOUDSDK_CONFIG` is set
- **THEN** that directory is checked before the platform default

### Requirement: Completion calls retry, fall back, cache, and time out

`LiteLLMProvider.complete` SHALL enforce the configured request timeout at the
adapter boundary, retry transient failures within its bounded budget, switch to
`fallback_model` when the primary is exhausted, and place explicit cache
breakpoints on the shared prefix for supported routes. Cache usage SHALL land
on `ProviderResult`.
<!-- anchor: provider.complete -->

#### Scenario: provider SDK ignores its timeout
- **WHEN** the underlying completion call remains blocked past the configured
  timeout
- **THEN** the adapter raises a timeout error reporting the measured wait, and does
  not retry it — an identical request against an identical budget can only fail the
  same way — while a configured fallback model is still tried

#### Scenario: the account is out of prepaid credit
- **WHEN** a route refuses the request because the balance cannot cover it —
  prepaid routes reserve prompt + `max_tokens` before generating, so an uncapped
  request reserves the model's full output ceiling
- **THEN** it is treated as permanent and tried once, not retried per lens: the
  balance cannot grow mid-review, so every retry fails identically

#### Scenario: a failed call reports what it cost
- **WHEN** a completion fails after exhausting its retries
- **THEN** the raised error carries the attempt count, so instrumentation
  distinguishes a budget-burning failure from a first-try one

#### Scenario: the call completes just past the deadline
- **WHEN** the completion finishes within the platform timer's granularity after
  the deadline (coarse-timer platforms overshoot by ~15ms)
- **THEN** its outcome is honoured rather than discarded, so a paid response is not
  wasted and a permanent error is not masked as a retryable timeout

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

#### Scenario: the documented default and the resolved one disagree
- **WHEN** a provider's resolved default stops matching the seconds the Action
  input and the Action how-to advertise
- **THEN** the test suite fails, because a silently reclassified provider leaves
  every timeout floor green while breaking the promise a user read
