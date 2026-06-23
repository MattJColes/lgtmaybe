# Auth Model

lgtmaybe supports seven hosted providers plus local ollama, and an
`openai-compatible` escape hatch for any server speaking the OpenAI `/v1` wire
format (DeepSeek's API, llama.cpp, LM Studio, vLLM). The design principle is **no
static cloud credentials**: cloud providers use ambient, short-lived tokens;
key-based SaaS providers (openai, anthropic, openrouter, zai) require an API key that
stays in secrets rather than being committed to config. Azure straddles both — it
prefers ambient Azure AD (Entra) credentials but accepts a resource key — and
always needs the resource endpoint (`AZURE_API_BASE`), since each Azure OpenAI
deployment lives at its own URL.

## Why keyless for cloud

Static credentials — an AWS access key pair or a GCP service-account JSON file
— have a fixed lifetime and broad scope. If they leak from a CI log, a secrets
manager misconfiguration, or a compromised runner, an attacker retains access
until the key is manually rotated.

OIDC (AWS), Workload Identity Federation (GCP), and federated credentials on an
Entra app (Azure) issue tokens that:

- Expire in minutes, not years
- Are scoped to a single workflow run
- Cannot be extracted from CI logs (they are never set as static environment
  variables)
- Are tied to the specific repository and branch via the OIDC claim set

For these reasons, lgtmaybe treats Bedrock and Vertex as **ambient-credential
only** providers. There is no `--api-key` flag for them. Azure defaults to the
same keyless path (GitHub OIDC → Entra, via `azure-identity`'s
`DefaultAzureCredential`) but additionally accepts a resource key for teams that
can't yet adopt federation.

## Chain of responsibility

When lgtmaybe starts, it resolves credentials for the selected provider using a
chain:

1. **Provider identity** — which provider was chosen?
2. **Ambient cloud creds** — for Bedrock and Vertex, the chain stops here.
   If no ambient creds exist, lgtmaybe fails immediately with a clear
   "how to auth this provider" message. It does not fall back to a key.
3. **API key** — for openai, anthropic, openrouter, and zai (GLM / Zhipu AI),
   lgtmaybe reads the key from the environment (`OPENAI_API_KEY`,
   `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `ZAI_API_KEY`). The `--api-key` flag
   can override this at the CLI. `zai` also accepts an optional `--api-base` /
   `ZAI_API_BASE` override for the China / coding-plan GLM endpoint; without it
   litellm's native `zai/` route targets the international endpoint.
4. **Azure** — always needs the resource endpoint (`AZURE_API_BASE` or
   `--api-base`). For the credential it prefers a key when one is present
   (`AZURE_API_KEY` / `--api-key`); otherwise it goes **keyless**, minting an
   Azure AD token from ambient creds (GitHub OIDC federation in CI, or a local
   `az login` / managed identity). If neither a key nor an ambient credential is
   available, lgtmaybe fails with a message naming both options.
5. **None** — ollama requires no credentials. Only `--api-base` is needed
   to reach the local or remote server.
6. **openai-compatible** — always needs the endpoint (`--api-base` or
   `OPENAI_COMPATIBLE_API_BASE`), since the whole point is to choose your own.
   The key is **optional**: hosted endpoints like DeepSeek take one
   (`--api-key` / `OPENAI_COMPATIBLE_API_KEY`), while local servers (llama.cpp,
   LM Studio, vLLM) need none — lgtmaybe sends a harmless placeholder key when
   you supply none, because the underlying OpenAI client rejects an empty one.

## Provider auth summary

| Provider | Credential type | How to supply |
|---|---|---|
| openai | API key | `OPENAI_API_KEY` env var or `--api-key` |
| anthropic | API key | `ANTHROPIC_API_KEY` env var or `--api-key` |
| openrouter | API key | `OPENROUTER_API_KEY` env var or `--api-key` |
| zai | API key (+ optional endpoint) | `ZAI_API_KEY` env var or `--api-key` (GLM / Zhipu AI); optional `ZAI_API_BASE` / `--api-base` for the China / coding-plan endpoint |
| bedrock | Ambient AWS creds | GitHub OIDC role or local `~/.aws`; IAM requires only `bedrock:InvokeModel*` |
| vertex | Ambient GCP creds | GitHub WIF or local ADC (`gcloud auth application-default login`) |
| azure | Ambient Azure AD creds (keyless) or API key, + endpoint | GitHub OIDC → Entra federated credential, or local `az login` / managed identity; or `AZURE_API_KEY`. Always with `AZURE_API_BASE` / `--api-base` |
| ollama | None | `--api-base` pointing to the local or remote server |
| openai-compatible | Optional key, + endpoint | `--api-base` / `OPENAI_COMPATIBLE_API_BASE` (e.g. `https://api.deepseek.com/v1` or `http://localhost:8000/v1`); key via `--api-key` / `OPENAI_COMPATIBLE_API_KEY`, or none for keyless local servers |

## Least-privilege IAM

For Bedrock, the minimum IAM policy is `bedrock:InvokeModel` and (if streaming)
`bedrock:InvokeModelWithResponseStream` on the specific model ARN. No other AWS
permissions are needed or requested.

For Vertex, `roles/aiplatform.user` on the project is sufficient.
`roles/editor` or `roles/owner` must not be used.

For Azure (keyless), the Entra app needs only the **Cognitive Services OpenAI
User** role on the Azure OpenAI resource — enough to call deployments, not to
manage them — plus a federated credential scoped to your repository. No owner or
contributor role is required.

## API keys in secrets, not config

For openai, anthropic, and openrouter, the key must live in a GitHub Actions
secret (or an equivalent secret store). It must never be committed to
`.lgtmaybe.yml` or any other file in the repository. lgtmaybe does not log or
display key values.
