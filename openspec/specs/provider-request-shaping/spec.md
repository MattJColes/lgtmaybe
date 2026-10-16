# provider-request-shaping Specification

## Purpose

How the litellm adapter (`providers/`) shapes each request for the route
it is sent to: which params the route accepts, how structured output
degrades when a schema is refused, and how a configured setting that never
reaches the model is disclosed rather than dropped in silence.

## Requirements

### Requirement: A rejected request param degrades, it does not fail the review

The adapter SHALL degrade a request param the model refuses and re-send once
rather than fail — `temperature` when only the default value is accepted,
`reasoning_effort` when the route refuses the effort field it becomes (judged
before the schema, whose matcher would otherwise claim `output_config.effort`
and drop the wrong param), and the structured-output `response_format` under
either spelling of the field it becomes: the Anthropic layer's
`output_config.format` or the Converse-level `outputConfig`. It SHALL also
degrade a refusal of the tool shape litellm rewrote the schema into on the
caller's behalf — a tool-field refusal arriving while a `response_format` is in
flight with no tool of the adapter's own on the request, and the caller holding
none either — and a refused `prompt_cache_key`. `drop_params` strips none of
these, so the refusal is only visible in the error. Each
outcome SHALL be remembered for THAT MODEL's later calls only, and losing the
schema SHALL be announced once — silently, it reads as a model that stopped
honouring it. litellm's stdout banner SHALL be suppressed.
<!-- anchor: provider.param-drop -->

#### Scenario: litellm converted the schema into its own forced tool
- **WHEN** a Bedrock model with no native structured-output support 400s on
  `toolConfig`, the tool having been built by litellm rather than the adapter
- **THEN** the review degrades to prompt-instructed JSON rather than dying on a
  permanent 400 no recovery could see

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

### Requirement: Structured output degrades one rung at a time

Every trigger that doubts a model's structured output SHALL step it down ONE
rung — schema, then the SAME schema as a forced tool call, then
prompt-instructed JSON — never straight to the floor. The triggers are a 400
naming the field, a reply whose schema mode decoded to nothing, and the engine
reporting a well-formed reply that was not findings; each is a single
observation about the mechanism in play, not about enforcement as a whole. The
floor SHALL cost two observations, and the tool rung SHALL be skipped only when
it cannot exist — a `response_format` carrying no JSON Schema, or a route that
has already refused a tool. Reaching the floor SHALL retire the tool with it.

One rung per observation is what bounds the blast radius. The fan-out shares one
provider instance, the outcome is keyed by model, and nothing lifts it for the
rest of the run, so a single empty reply on one lens used to cost enforcement
for every later call — including on Bedrock, whose Converse endpoint implements
structured output AS the forced tool call that was being skipped.
<!-- anchor: provider.schema-ladder -->

#### Scenario: one lens gets an empty reply under the schema
- **WHEN** a schema-mode call returns an empty body
- **THEN** it is re-sent as a forced tool call, and the rest of the fan-out stays
  schema-enforced rather than dropping to prompt-instructed JSON

#### Scenario: the engine reports a reply that was not findings
- **WHEN** the engine asks the adapter to stop trusting the schema mode
- **THEN** the model steps down to the forced tool call, and only a second such
  ask gives the schema up

#### Scenario: the rung does not exist
- **WHEN** the model's `response_format` carries no JSON Schema, so no tool could
  enforce it
- **THEN** the drop goes straight to the floor rather than leaving the model
  sending the shape that was just rejected

### Requirement: The schema mechanism is chosen for the route before the first call

The adapter SHALL consult the same supported-params list litellm's `drop_params`
consults, once per model, before the first request that carries a schema: a
route that omits `response_format` but takes `tools` gets the schema as the
forced tool call up front, a route with neither drops it up front and announces
the downgrade, and a lookup failure leaves the schema on so the request-level
recoveries still apply. Without this a route litellm strips the field from (zai)
reviewed with no schema while the adapter reported enforcement was on — no 400,
no empty body, nothing for the existing recoveries to see.
<!-- anchor: provider.schema-route -->

#### Scenario: the route lists tools but not response_format
- **WHEN** the first schema-carrying call for a zai model is made
- **THEN** it goes out as a forced tool call, `sends_response_format` stays true,
  and no round-trip is spent learning it

#### Scenario: the route lists neither
- **WHEN** a route takes no structured-output mechanism at all
- **THEN** the schema is dropped before the first call and the drop is announced

### Requirement: A Claude request that turns thinking on carries no temperature

The factory SHALL withhold `temperature` from a request whose `reasoning_effort`
turns extended thinking on for a Claude model on the anthropic, bedrock or
vertex route, and SHALL say so: Anthropic refuses a temperature beside
thinking (`temperature` may only be set to 1 when thinking is enabled) and
litellm forwards both. An effort that switches thinking off (`none`,
`default`), no effort, or a non-Claude model keeps the temperature. The
adapter's temperature-rejection matcher SHALL recognise Anthropic's wording as
the backstop for a gateway route the factory does not recognise.
<!-- anchor: provider.thinking-sampling -->

#### Scenario: an effort is configured on the anthropic route
- **WHEN** `reasoning_effort: medium` and the default `temperature: 0.0` are set
- **THEN** the request carries the effort and no temperature, and the omission
  is logged

#### Scenario: the effort is `none`
- **WHEN** `reasoning_effort: none` is set on a Claude model
- **THEN** the temperature is sent, because thinking is off

### Requirement: A configured temperature that never reaches the model is disclosed

The adapter SHALL record, per model, a configured `temperature` that did not
reach the model — stripped by litellm's `drop_params` before the request, or
refused by the model and re-sent without it — and expose it as
`temperature_dropped()`, adapter-only and feature-detected like
`schema_dropped()`; the engine SHALL then render a `temperature not sent`
segment on the summary line beside the model (`{temperature}` in
`summary_template`), because the determinism default is what makes a review
reproducible — a segment, never a notice, so a clean review keeps its LGTM. The strip
SHALL be judged by mapping the request params through litellm's own
`get_optional_params` with and without the temperature (the effort included,
since the gpt-5 rule turns on it), never by predicting it from a capability
list. Detection SHALL NOT change the request, and a failed lookup SHALL report
nothing.
<!-- anchor: provider.temperature-reach -->

#### Scenario: a Bedrock model that takes no sampling params
- **WHEN** `temperature: 0.0` is configured for a Converse model flagged
  `supports_sampling_params: false`
- **THEN** the request is unchanged, and the summary line carries
  `temperature not sent` beside the model — a clean review still opens with LGTM

#### Scenario: a route that takes the temperature
- **WHEN** the route forwards the configured temperature
- **THEN** nothing is flagged, and the summary is byte-identical

### Requirement: The bedrock schema is narrowed to the subset its validator takes

On the bedrock route the structured-output schema SHALL be pruned to the
keywords Bedrock's validator names — the OpenAI strict subset — rather than
stripped of the ones a bug report reached: anything outside it is an extra input
that 400s the whole request before the model ever runs. A whitelist because a
blacklist does not converge; removing `minimum` left `default`, which pydantic
emits for every field carrying one, on the wire for the next report to find. The
structural keywords SHALL survive, since a schema without its `$defs`, `$ref` or
`enum` constrains nothing. The request SHALL otherwise carry the exact dict
litellm would derive from the pydantic class itself, applied per effective model
— a non-bedrock fallback keeps the class, whose own route derives its own schema
dialect from it — and the prune SHALL remove only keywords, never a property
that happens to share a keyword's name, and never descend into a keyword holding
literal values. Nothing is enforced less: the same pydantic model re-checks every
pruned constraint when the reply is parsed.
<!-- anchor: provider.schema-subset -->

#### Scenario: a bedrock model is asked for structured output
- **WHEN** a review call goes out to a bedrock model with a pydantic
  `response_format` whose fields carry `ge`/`le` bounds and defaults
- **THEN** the request carries the schema litellm would derive minus both, and
  the reply is still validated against the full model on parse

#### Scenario: the tool-mode fallback re-sends the schema
- **WHEN** the route refuses `response_format` and the schema is re-sent as a
  forced tool call
- **THEN** the tool's parameters carry the pruned schema too, so the same
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
