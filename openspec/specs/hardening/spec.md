# hardening Specification

## Purpose

The reviewer's own defenses, so a malicious PR can't subvert *us*: prompt
injection defense with delimiter break-out neutralisation
(`engine/injection.py`), secret redaction before anything leaves for the LLM
(`engine/redact.py`), and structured-output parsing that recovers leniently
but validates strictly (`engine/parse.py`). Diff content is untrusted
everywhere it flows.

## Requirements

### Requirement: The diff is wrapped as untrusted data

The diff SHALL enter the prompt only inside a delimited untrusted-data block,
and forged delimiters in the content — any marker family, any case — SHALL be
neutralised so an attacker diff cannot break out of the data block or forge a
different block's markers.
<!-- anchor: hardening.wrap-diff -->

#### Scenario: attacker forges the end delimiter
- **WHEN** a diff contains a `DIFF_END` (or any block's) marker
- **THEN** the marker is neutralised before the diff is embedded

### Requirement: Stated intent gets the same posture

PR title/body/commit text SHALL be wrapped in its own untrusted block
(`INTENT_START`/`INTENT_END`, both marker families neutralised) and sent only
on the intent call — author-controlled text never rides along unmarked.
<!-- anchor: hardening.wrap-intent -->

#### Scenario: intent text tries to steer the reviewer
- **WHEN** a PR description contains instructions to the model
- **THEN** they arrive inside the neutralised intent block, marked untrusted

### Requirement: Static-analysis hints are their own untrusted block

Tool findings SHALL enter lens calls only as a wrapped HINTS block with its
own neutralised marker family, framed as "confirm, contextualise, or discard".
<!-- anchor: hardening.wrap-hints -->

#### Scenario: hints accompany a batch
- **WHEN** static analysis produced findings for a batch
- **THEN** they are prepended wrapped, never as trusted instructions

### Requirement: Secrets are redacted before egress

Diffs, intent text, and expanded context SHALL be redacted before leaving for
the LLM: AWS/OpenAI/GitHub (classic + fine-grained)/Slack/Google/Stripe keys,
PEM private-key blocks, and quoted password / `Authorization` /
connection-string credentials.
<!-- anchor: hardening.redact -->

#### Scenario: a committed key would leave the machine
- **WHEN** a diff hunk contains an AWS access key
- **THEN** the provider receives the hunk with the key replaced, never the key

### Requirement: Parsing recovers leniently, validates strictly

Model output SHALL be parsed as JSON with repair for common wrappers (fences,
prose preamble), then validated against the strict finding schema — recovery
never widens what is accepted, and unparseable output yields an error, not
invented findings.
<!-- anchor: hardening.parse -->

#### Scenario: model wraps JSON in a code fence
- **WHEN** the reply is valid findings JSON inside markdown fences
- **THEN** parsing succeeds; fields still validate against the strict schema
