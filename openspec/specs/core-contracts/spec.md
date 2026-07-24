# core-contracts Specification

## Purpose

The frozen contracts everything else codes against: the hexagonal ports in
`core/ports.py` (the seams between the engine and the outside world) and the
strict pydantic models in `core/models.py` (the wire format between all
tracks). These froze in the foundation step; adapters and the engine are built
against them, and fakes drop in through them.

## Requirements

### Requirement: Provider port

`ProviderClient` SHALL be the only seam to an LLM backend: one `complete()`
call taking chat messages and a model id and returning a `ProviderResult`.
Adapters (litellm) and fakes implement it; nothing else in the engine talks to
a model.
<!-- anchor: core.provider-port -->

#### Scenario: engine calls any backend the same way
- **WHEN** the engine needs a completion, for any provider
- **THEN** it calls `ProviderClient.complete(messages, model, **opts)` and gets
  a `ProviderResult` back, with no provider-specific branching in the engine

### Requirement: GitHub gateway port

`GitHubGateway` SHALL expose PR context retrieval and review posting as the
only GitHub seam. Implementations fetch the diff via API only — PR code is
never checked out or executed (fork safety under `pull_request_target`).
<!-- anchor: core.gateway-port -->

#### Scenario: review round-trip through the port
- **WHEN** a review runs against a PR
- **THEN** context arrives via `get_pr_context()` and results leave via
  `post_review(findings, summary)` — no other GitHub access from the engine

### Requirement: Engine port

`ReviewEngine` SHALL map `(PRContext, ReviewConfig)` to
`(list[ReviewFinding], summary)`. Callers (CLI, Action, slash commands) depend
on this signature, not on the pipeline inside.
<!-- anchor: core.engine-port -->

#### Scenario: any caller, one entrypoint
- **WHEN** the CLI, the Action, or a slash command wants a review
- **THEN** it injects its ports and calls `review(ctx, cfg)`

### Requirement: Findings are structured output only

`ReviewFinding` SHALL be the only shape a finding takes: severity, file, line,
side, title, body, optional suggestion, verbatim `anchor` line, `anchored`
flag, 0-10 `confidence`, and the originating lens `category`. Models are
strict (`extra="forbid"`), so drifted or injected fields are rejected — prose
is never parsed.
<!-- anchor: core.finding -->

#### Scenario: model returns an unexpected field
- **WHEN** the LLM's JSON carries a field the contract doesn't declare
- **THEN** validation rejects it rather than silently accepting it

### Requirement: One config surface with ordered severities

`ReviewConfig` SHALL be the single knob surface for a review (provider, model,
filters, caps, toggles); `Severity` SHALL order `info < low < medium < high <
critical` so floors like `min_severity` compare with `>=`.
<!-- anchor: core.config -->

#### Scenario: severity floor filters findings
- **WHEN** `min_severity` is `medium`
- **THEN** `low` and `info` findings are dropped before posting

### Requirement: Nine built-in lenses, preset-shaped fan-out

`ReviewCategory` SHALL enumerate the nine built-in lenses (security,
correctness, deprecation, tests, documentation, performance, complexity,
intent, ponytail). `ReviewPreset` SHALL shape the fan-out: `fast` (default)
covers seven of the nine (tests and documentation excluded) in four calls
when the provider can overlap work, three with one worker; `full` runs one
call per lens.
<!-- anchor: core.lenses -->

#### Scenario: default preset batches the lenses
- **WHEN** a review runs with no preset override
- **THEN** the seven fast-preset lenses fan out as four concurrent calls
