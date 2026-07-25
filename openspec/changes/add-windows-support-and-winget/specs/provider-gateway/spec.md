## MODIFIED Requirements

### Requirement: Credentials resolve by chain, fail with instructions
<!-- anchor: provider.credentials -->

Resolution SHALL try the chosen provider's native mode first (ambient cloud
creds for bedrock/vertex/azure), then an API key from flag/env; ollama needs
neither; openai-compatible needs an `api_base` with the key optional
(placeholder when absent). Vertex ambient credential probing MUST prefer
`CLOUDSDK_CONFIG`, then use `%APPDATA%\gcloud` on Windows or
`~/.config/gcloud` on POSIX. Static cloud keys (AWS keys, service-account JSON)
are never accepted or required; exhaustion fails with a clear
"how to auth this provider" message.

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
