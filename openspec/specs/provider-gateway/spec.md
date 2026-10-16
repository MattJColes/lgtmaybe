# provider-gateway Specification

## Purpose

The LLM adapter track: one litellm-backed client behind the `ProviderClient`
port, a factory that builds it from the `--provider` flag, and credential
resolution as a chain of responsibility — the wedge being keyless OIDC/WIF for
Bedrock/Vertex/Azure with no static cloud keys, ever. How each request is
shaped for its route (params, schema mechanism) lives in
`provider-request-shaping`.
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
`~/.config/gcloud` on POSIX, then defer to google-auth's own chain. Keyless
Azure MUST hand litellm a refreshing token provider, not one fetched token.
Static cloud keys (AWS keys, service-account JSON)
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

#### Scenario: Vertex on a GCP runtime
- **WHEN** only metadata-server credentials exist (GCE, GKE, Cloud Run)
- **THEN** ambient Vertex authentication is detected

#### Scenario: keyless Azure has no ambient credential
- **WHEN** no key is supplied and `DefaultAzureCredential` finds no credential
- **THEN** the run fails at start-up with the keyless guidance and the SDK's detail

### Requirement: A subscription sign-in is used, never started, by a review

The `chatgpt` provider SHALL use the sign-in `lgtmaybe login chatgpt` stored in
litellm's token file, and a review MUST never start litellm's device-code
prompt: it prints to stdout (breaking MCP stdio and `--format json`) and polls
in every lens call. The resolver SHALL renew the sign-in once before the
fan-out, and fail at start-up naming the login command when it cannot.
<!-- anchor: provider.subscription-sign-in -->

#### Scenario: no stored sign-in
- **WHEN** `--provider chatgpt` runs with no token file, as on a CI runner
- **THEN** it fails at start-up naming `lgtmaybe login chatgpt` and the path read

#### Scenario: the refresh token is revoked
- **WHEN** litellm cannot renew the stored sign-in
- **THEN** the run fails with the login command and nothing is printed to stdout

### Requirement: Completion calls retry, fall back, cache, and time out

`LiteLLMProvider.complete` SHALL enforce the configured request timeout at the
adapter boundary, retry transient failures within its bounded budget, switch to
`fallback_model` when the primary is exhausted, and place explicit cache
breakpoints on the shared prefix for supported routes. Cache usage SHALL land
on `ProviderResult`, as SHALL the reasoning-token count where the route reports
one — on successful calls, not only on the truncated ones that name it as a
cause, since a ceiling-hitting call offers no healthy call to compare against.
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

### Requirement: A caller may own a truncation and name the model

The adapter SHALL let a caller that holds a better remedy for a truncation ask
for that one failure back rather than switching model beneath it, and SHALL let
one call name the model it runs on. Both exist for the engine, which holds the
payload and the token counts a truncation carries and so can shrink the payload
or lower the thinking budget on the model the user chose — remedies this adapter
cannot reach. The resolved fallback SHALL be readable, so the caller can spend
the escalation itself instead of re-billing the primary to reach it; a call that
already names its model SHALL NOT fall back again, and every failure the caller
did not claim SHALL still fall back here. Whichever model answered SHALL land on
`ProviderResult`: it is unknowable outside this adapter, and it is the only
thing separating a rescued review from an ordinary one.
<!-- anchor: provider.escalation -->

#### Scenario: the caller owns the truncation remedy
- **WHEN** a call asking to defer truncation runs out of output tokens
- **THEN** the failure is raised to the caller unswitched

#### Scenario: another failure under the same request
- **WHEN** that call fails for any other reason
- **THEN** the fallback model is tried as it always was

#### Scenario: the escalation itself fails
- **WHEN** a call naming the model to run on fails
- **THEN** nothing is fallen back to — that call already is the fallback

#### Scenario: neither option is used
- **WHEN** a caller asks for neither
- **THEN** the request is byte-identical to one made before either existed

#### Scenario: a fallback answered
- **WHEN** any call completes on a model other than the one asked for
- **THEN** the result names the model that answered it

### Requirement: The reasoning spend is reported against what it was drawn from

A response's reasoning-token count SHALL be reported as **unknown** when the
route gives no breakdown — never as zero, which asserts the model did no
thinking. The `max_tokens` ceiling the request actually carried SHALL ride the
result alongside it, so the spend can be read as a SHARE of its own budget: the
two settings are coupled (one ceiling pays for thought and answer alike), and
neither raw count says whether the pair has headroom left.
<!-- anchor: provider.reasoning-accounting -->

#### Scenario: the route reports no breakdown
- **WHEN** a successful response carries no `completion_tokens_details`
- **THEN** the count is unknown, and nothing is claimed about the thinking done

#### Scenario: no ceiling was configured
- **WHEN** a request goes out with no `max_tokens`
- **THEN** no ceiling rides the result, and no share is computed from one

### Requirement: Backoff matches what failed

Retry backoff SHALL be chosen by the failure. A capacity rate limit SHALL back
off far enough to reach a fresh metering window, and SHALL prefer the server's
own `Retry-After` when one is sent, clamped so a long hint cannot consume the
run. Every other transient failure SHALL keep the sub-second ladder. The retry
budget SHALL be weighed against the wait about to be taken, so no backoff is
slept past it.
<!-- anchor: provider.backoff -->

#### Scenario: the gateway meters the key per minute
- **WHEN** a call is refused with a capacity 429 and no retry hint
- **THEN** the attempts are spread across minutes rather than seconds, so they do
  not all land in the window that just refused them

#### Scenario: the gateway says when to come back
- **WHEN** a 429 carries a `Retry-After` header, in either delta-seconds or
  HTTP-date form
- **THEN** that wait is honoured up to a ceiling, since nothing computed locally
  can beat the server's own answer

#### Scenario: the hint would outlast the call's budget
- **WHEN** the wait a retry is about to take would carry the call past its
  retry budget
- **THEN** the call ends there rather than sleeping through the budget it was
  given

#### Scenario: a brief connection failure
- **WHEN** a call fails on something other than a rate limit — a reset, a 5xx, a
  local server still warming up
- **THEN** it retries in fractions of a second, because the condition is gone by
  the time the next request lands

### Requirement: The adapter reports how much prompt its model can take

The adapter SHALL expose `input_budget()`: ollama's `num_ctx` when it sends
one, else the `max_input_tokens` litellm's model map records for the resolved
model string, less the output ceiling the adapter will send; None when the map
has no entry or no window, so an unknown model never shrinks a budget. A
ceiling that fills the whole window SHALL cap the budget at half the window,
with a warning naming both, never None. A
prompt the backend refuses for its window SHALL be raised as
`ProviderInputTooLarge` — never retried in place, never stamped unrecoverable,
and handed to a caller that owns the remedy exactly as a truncation is.
<!-- anchor: provider.input-budget -->

#### Scenario: an ollama model
- **WHEN** the factory sent `num_ctx` and a finite `max_tokens`
- **THEN** the budget is their difference, whatever the map says of the model

#### Scenario: the output ceiling fills the window
- **WHEN** `max_tokens` is at least `num_ctx` (8192 under the default 16,384)
- **THEN** the budget is half the window and a warning names both numbers

#### Scenario: the backend refuses the prompt
- **WHEN** litellm raises `ContextWindowExceededError`
- **THEN** the call fails once as `ProviderInputTooLarge`, and the engine splits

### Requirement: A blown output ceiling is named, not retried

A completion that stopped because it ran out of output tokens SHALL be raised
as its own failure naming `max_tokens` as the ceiling reached — usually a value
the user set, not the model's own — plus the reasoning-token count where the
route reports it, and SHALL carry the cut-off body so the engine can salvage the
findings finished before the cut, and both counts as data so a caller can tell a
long answer from exhausted thinking without reading the message. It SHALL NOT be
retried — at temperature 0 the identical request reaches the identical ceiling,
and each attempt costs a full ceiling-length generation — while a configured
fallback model is still tried.
<!-- anchor: provider.truncation -->

#### Scenario: the model generates to its output limit
- **WHEN** a completion returns with a `length` finish reason
- **THEN** the call fails naming the token count reached and `max_tokens`, is not
  retried, and carries the truncated body

#### Scenario: a reasoning model spends the budget on thought
- **WHEN** the route reports reasoning tokens on a truncated completion
- **THEN** the failure names them and carries them beside the ceiling as numbers,
  because that — not diff size — is why a small diff hit the ceiling

#### Scenario: the route reports no reasoning breakdown
- **WHEN** a truncated completion carries no reasoning count
- **THEN** the failure carries none either, never zero — "it never said" must not
  read as "it thought nothing"

### Requirement: A ceiling hit is detected even when the route will not say

Detection SHALL read the finish reason where the route reports it plainly, and
SHALL additionally treat a response that generated to a ceiling lgtmaybe itself
configured as a ceiling hit. litellm rewrites a finish reason it does not
recognise to `stop`, and ollama reports nothing useful, so on those routes
spending the whole cap is the only evidence there is — and without this a cut-off
call renders in the profile as a clean, cheap success, which is what had
benchmark tooling counting zero truncations. Judged only against a ceiling
lgtmaybe set: with none configured a long answer is just a long answer.
<!-- anchor: provider.ceiling-detection -->

#### Scenario: the route misreports why it stopped
- **WHEN** a provider reports a ceiling hit under a name litellm maps to `stop`
- **THEN** spending the configured ceiling is itself read as the truncation, so
  the profile marks the call rather than showing a clean row

#### Scenario: the answer simply finished
- **WHEN** a completion stops below the configured ceiling
- **THEN** it is an ordinary success, never a truncation

#### Scenario: nothing was capped
- **WHEN** no ceiling was configured for the call
- **THEN** no output length is read as a truncation

### Requirement: Defaults are provider-aware

Timeouts SHALL default long for providers that may front a slow model —
local-capable ones (ollama/openai-compatible) and openrouter, a gateway to
arbitrary models including slow reasoning ones — and short for direct cloud
providers; a local-capable default SHALL additionally scale with the fan-out
width, bounded by the whole-review deadline, because a queued call spends that
budget waiting rather than working; the litellm model string is derived per
provider so users give bare model ids.
<!-- anchor: provider.defaults -->

#### Scenario: no timeout configured
- **WHEN** `timeout` is unset and the provider is ollama
- **THEN** the generous (long) default applies, not the cloud one

#### Scenario: openrouter gets the generous default
- **WHEN** `timeout` is unset and the provider is openrouter
- **THEN** the generous (long) default applies, not the cloud one

#### Scenario: a local fan-out queues behind one slot
- **WHEN** `timeout` is unset, the provider is local-capable, and the fan-out is
  wider than one
- **THEN** the default is multiplied by that width, so a call whose wait is spent
  in the server's queue still has a full budget for the work itself

#### Scenario: the widened budget exceeds the review's own deadline
- **WHEN** that scaled default exceeds `max_review_seconds`
- **THEN** the resolved budget is the lesser of the scaled default and the
  deadline, floored at the provider's own default — so a deadline shorter than
  that default does not shrink it — and nothing is clamped when the deadline is
  disabled. It bounds the budget, not the wall clock: the deadline gates when a
  call may start rather than cutting a running one short

#### Scenario: the documented default and the resolved one disagree
- **WHEN** a provider's resolved default stops matching the seconds the Action
  input and the Action how-to advertise
- **THEN** the test suite fails, because a silently reclassified provider leaves
  every timeout floor green while breaking the promise a user read

### Requirement: A runaway-prone route gets a finite output ceiling

`max_tokens` unset SHALL resolve to one finite ceiling for ollama,
openai-compatible and openrouter, and to none for the first-party APIs, because a
model under structured output can decode without terminating and the only thing
that would otherwise stop it is the deliberately generous per-call timeout.
The failure follows from the model and the structured-output task rather than
from where the model runs: measured, a hosted route returned a 393k-token
response that parsed into 699 false positives on a diff with nothing wrong in it. A configured value SHALL win,
`0` SHALL mean explicitly uncapped, and the resolved ceiling and its source SHALL
be announced before the first call so a truncation is not read as a number the
user chose.
<!-- anchor: provider.output-ceiling -->

#### Scenario: no ceiling configured on a runaway-prone route
- **WHEN** `max_tokens` is unset and the provider is ollama, openai-compatible or
  openrouter
- **THEN** the same finite ceiling applies, so a non-terminating decode is cut off
  in seconds and reported as a truncation rather than as a timeout half an hour later

#### Scenario: no ceiling configured on a first-party API
- **WHEN** `max_tokens` is unset and the provider is a first-party cloud API
- **THEN** no ceiling is sent, and the model's own applies — a long findings
  payload is never truncated for a problem that route has not shown

#### Scenario: the user turns the ceiling off
- **WHEN** `max_tokens` is `0`
- **THEN** no ceiling is sent at all, and the run is announced as uncapped
