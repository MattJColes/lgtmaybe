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
on the intent call — author-controlled text never rides along unmarked. Any
not-visible file list riding that block SHALL be neutralised with it: filenames
are author-controlled too.
<!-- anchor: hardening.wrap-intent -->

#### Scenario: intent text tries to steer the reviewer
- **WHEN** a PR description contains instructions to the model
- **THEN** they arrive inside the neutralised intent block, marked untrusted

#### Scenario: a filename forges the block delimiter
- **WHEN** a PR changes a file whose name embeds an `INTENT_END` marker
- **THEN** it is neutralised like the prose, so the path list cannot close the
  block early

### Requirement: Static-analysis hints are their own untrusted block

Tool findings SHALL enter lens calls only as a wrapped HINTS block with its
own neutralised marker family, framed as "confirm, contextualise, or discard".
<!-- anchor: hardening.wrap-hints -->

#### Scenario: hints accompany a batch
- **WHEN** static analysis produced findings for a batch
- **THEN** they are prepended wrapped, never as trusted instructions

### Requirement: The reflection audit treats its inputs as untrusted too

The audit call SHALL carry the same posture as a lens call: the diff and the
grounding head text are neutralised against marker forgery, and the auditor is
told not to follow instructions found in them — otherwise a diff could steer
the one pass that can drop every finding.
<!-- anchor: hardening.audit-untrusted -->

#### Scenario: a diff tells the auditor to drop everything
- **WHEN** a diff embeds instructions aimed at the false-positive auditor
- **THEN** they arrive neutralised, under an explicit untrusted-data guard

### Requirement: Secrets are redacted before egress

Diffs, intent text, and expanded context SHALL be redacted before leaving for
the LLM: AWS/OpenAI/GitHub (classic + fine-grained)/Slack/Google/Stripe keys,
PEM private-key blocks, and quoted password / `Authorization` /
connection-string credentials. The prompt SHALL identify the replacement marker
as the reviewer's own, so no lens reports it as a leaked secret or a
placeholder left in the source.
<!-- anchor: hardening.redact -->

#### Scenario: a committed key would leave the machine
- **WHEN** a diff hunk contains an AWS access key
- **THEN** the provider receives the hunk with the key replaced, never the key

#### Scenario: the marker reaches the model
- **WHEN** a lens reads a diff that redaction has rewritten
- **THEN** the prompt has told it the marker is not the author's code

### Requirement: Parsing recovers leniently, validates strictly

Model output SHALL be parsed as JSON with repair for common wrappers (fences,
prose preamble), then validated against the strict finding schema — recovery
never widens what is accepted, and unparseable output yields an error, not
invented findings. That error SHALL name WHICH fault it was — a reply that
never attempted JSON, one whose JSON does not decode, one that decoded to a
shape that was never findings, one the strict schema refused, an empty reply,
and one cut off mid-container are six different problems with six different
fixes. The truncated case in particular SHALL NOT be reported as unparseable.
Bracket-bearing prose SHALL NOT be reported as malformed JSON: prose is full
of brackets that were never a container.
<!-- anchor: hardening.parse -->

#### Scenario: model wraps JSON in a code fence
- **WHEN** the reply is valid findings JSON inside markdown fences
- **THEN** parsing succeeds; fields still validate against the strict schema

#### Scenario: the reply is cut off mid-findings
- **WHEN** a reply ends inside an unclosed array, so its earlier complete
  objects parse but fail the strict schema
- **THEN** it is reported as truncated, not as a schema violation — the missing
  tail is the cause and the validation failure only its symptom

#### Scenario: findings survive the cut
- **WHEN** a truncated reply contains whole findings before the cut
- **THEN** every one that validates is recovered and posted, the half-written
  trailing object is not, and the recovery travels with the truncation report
  so the lens is never read as complete

#### Scenario: the model answered in prose
- **WHEN** a reply never opens a JSON container at all
- **THEN** it is reported as prose, not as a schema violation — nothing was
  rejected, the format was never attempted

#### Scenario: one finding, nothing cut off
- **WHEN** a complete reply is a single bare finding object
- **THEN** it parses as the whole answer, unchanged by the recovery path

### Requirement: A parse failure is diagnosable after the fact

A failed review call SHALL report its parse-failure shape wherever the failure
already travels — the log, the profile row, and the review notice — so the fault
is nameable without re-running the review, and SHALL report the reply's length
in the log. The reply body itself SHALL NOT be logged by default, and SHALL be
redacted and length-capped when debug logging asks for it. Where several calls
failed, the notice SHALL name the MOST COMMON failure rather than the last, so
one odd failure cannot mask a wave of identical ones.
<!-- anchor: hardening.parse-diagnosis -->

#### Scenario: a lens returns output that will not parse
- **WHEN** a review call succeeds but its reply is not findings JSON
- **THEN** the failure is reported with its shape and the reply's length, and
  none of the reply's content

#### Scenario: the operator asks for the body
- **WHEN** debug logging is enabled
- **THEN** a capped excerpt of the reply is logged, redacted before it is cut so
  a split secret cannot escape the redactor

#### Scenario: one lens fails differently from the rest
- **WHEN** three calls return prose and a fourth hits a rate limit
- **THEN** the notice names the prose, because that is what most calls did
