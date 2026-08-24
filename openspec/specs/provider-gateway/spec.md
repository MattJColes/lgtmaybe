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

### Requirement: A rejected request param degrades, it does not fail the review

The adapter SHALL degrade a request param the model refuses and re-send once
rather than fail — `temperature` when only the default value is accepted, and
the structured-output `response_format` when the route rejects the field it
becomes (Bedrock Converse answers `output_config.format: Extra inputs are not
permitted`). `drop_params` cannot cover these: the capability map reports the
param supported, and the refusal is only visible in the error. A rejected
`response_format` SHALL first be re-sent as the SAME schema in the mechanism the
route does implement — a forced tool call, read back from its arguments — and
only a route refusing that too SHALL fall back to prompt-instructed JSON. Each
outcome SHALL be remembered for THAT MODEL's later calls, never for every model
the provider serves, and losing the schema SHALL be announced once: silently, it
is indistinguishable from a model that stopped honouring it. litellm's stdout
banner SHALL be suppressed, since stdout carries machine-readable output. The
engine MAY ask for the same drop, on the one trigger the adapter cannot see: a
reply that arrives well-formed and turns out not to be findings.
<!-- anchor: provider.param-drop -->

#### Scenario: the route rejects the structured-output field
- **WHEN** a Bedrock model 400s on the `output_config.format` field litellm
  derived from `response_format`
- **THEN** the same schema is re-sent as a forced tool call and the rest of the
  fan-out uses that shape up front, keeping enforcement instead of losing it

#### Scenario: another model shares the provider
- **WHEN** one model rejects the schema while a second model — a fallback, or
  the triage or reflect slot — is served by the same client
- **THEN** the second model keeps sending `response_format`, rather than losing
  it to a refusal from a model it never ran

#### Scenario: an unrelated error arrives on the same call
- **WHEN** a failure under those params names neither a rejected param nor a
  rejected value
- **THEN** it propagates unchanged, rather than being retried bare and masked

#### Scenario: a provider error is mapped during a machine-readable run
- **WHEN** litellm maps a provider error while `--format json` is in force
- **THEN** nothing is printed to stdout, so the findings array stays parseable

### Requirement: The bedrock schema is narrowed to the subset its validator takes

On the bedrock route the structured-output schema SHALL go out without the
numeric-bound keywords pydantic derives from field constraints (`minimum`,
`maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`): Bedrock's
structured-output validator treats them as extra inputs and 400s the whole
request before the model ever runs. The request SHALL otherwise carry the exact
dict litellm would derive from the pydantic class itself, applied per effective
model — a non-bedrock fallback keeps the class, whose own route derives its own
schema dialect from it — and the strip SHALL remove only keywords, never a
property that happens to share a keyword's name. Nothing is enforced less: the
same pydantic model re-checks the bounds when the reply is parsed.
<!-- anchor: provider.schema-subset -->

#### Scenario: a bedrock model is asked for structured output
- **WHEN** a review call goes out to a bedrock model with a pydantic
  `response_format` whose fields carry `ge`/`le` bounds
- **THEN** the request carries the schema litellm would derive minus the bound
  keywords, and the reply is still validated against the full model on parse

#### Scenario: the tool-mode fallback re-sends the schema
- **WHEN** the route refuses `response_format` and the schema is re-sent as a
  forced tool call
- **THEN** the tool's parameters carry the stripped schema too, so the same
  validator cannot reject the recovery

### Requirement: A configured param is never discarded in silence

The factory SHALL name at startup every param the user configured that litellm's
capability map says the resolved model will not accept. `drop_params` is on so
one unsupported param cannot fail a whole review; the cost is that a knob which
was never connected is otherwise indistinguishable from one that did not help.
The check is keyed off litellm's own maps rather than a per-param special case,
and judges only OpenAI-vocabulary params — a provider-native option (ollama's
`num_ctx`) is not litellm's to drop. As a deliberate, narrowly scoped exception
to normalising every provider through litellm, a reasoning budget litellm will
not forward to OpenRouter SHALL instead be sent as OpenRouter's own top-level
`reasoning` object — and never beside the flat param, which OpenRouter rejects.
<!-- anchor: provider.param-support -->

#### Scenario: the model has no channel for a configured param
- **WHEN** a reasoning budget is configured for a model litellm's map says has
  no reasoning channel
- **THEN** the run names the ignored param up front, rather than the budget
  vanishing and leaving a truncated review to be blamed on the diff

#### Scenario: OpenRouter fronts a model litellm's map does not know
- **WHEN** a reasoning budget is configured on openrouter and litellm would drop
  it — its transformation forwards the flat param only for models already flagged
  reasoning-capable, which the newest models are not
- **THEN** the budget goes out in OpenRouter's native `reasoning` object, and the
  flat param is not sent alongside it

#### Scenario: the configured value has no equivalent on the route
- **WHEN** a configured value is outside the route's own vocabulary
- **THEN** it is reported as ignored rather than translated into a nearby value,
  which would buy a budget nobody asked for

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
