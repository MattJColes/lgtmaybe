---
description: What lgtmaybe sends to model providers, including optional context and specs, and how it redacts secrets before egress.
---

# Data and Privacy

This page explains what lgtmaybe sends to a model
[provider](../reference/glossary.md#provider), what it
[redacts](../reference/glossary.md#redaction) first, how local mode differs, and
how credentials are handled.

## What Is Sent to the LLM Provider

Under the default `fast` [preset](../reference/glossary.md#preset), lgtmaybe
makes five core review calls per diff [batch](../reference/glossary.md#batch).
Under `full`, it makes one per built-in category. A matching committed spec adds
a separate spec-review call, and a [reflection](../reference/glossary.md#reflection)
pass follows. These calls can contain:

- **The PR diff**: the unified diff of changed files, after generated files,
  lockfiles, minified assets, and vendored code have been skipped. File paths
  appear in the diff headers. Each batch also lists the PR's changed files that
  its own diff does not show.
- **Surrounding context lines**: unchanged lines around each changed
  [hunk](../reference/glossary.md#hunk), read from the head revision (the PR's
  latest commit) of the **changed files only**. They give the model the
  enclosing function and definitions, which cuts false positives.
    - Before each hunk, lgtmaybe adds up to `context_lines` lines (default 20,
      `0` disables it). It extends that up to the enclosing function or class
      signature where that can be found.
    - After each hunk, it adds about a quarter as many.
    - The amount shrinks as the diff grows, and the text is redacted like the
      diff.
    - On GitHub it is fetched read-only through the API. Pull request code is
      never checked out or executed.
- **The PR's stated intent**: the PR title, description, and the first line of
  each commit message. On the CLI, it is the commit subjects from your local
  `git log`. It feeds the intent check ("does the PR do what it says?"). It is
  redacted like the diff and wrapped as untrusted data. It is sent on only one
  review call: the correctness call under `fast`, or the dedicated intent
  [lens](../reference/glossary.md#lens) under `full`. The change overview's
  calls also send it (see below).
- **A system prompt**: the fixed instructions that tell the model to return
  structured JSON findings.
- **A project paragraph**, if the `project` block is set: its stage guidance,
  `domain` and `notes`, as written in `.lgtmaybe.yml`. Delimiter tokens in it
  are neutralised (made harmless, see
  [Prompt-Injection Defence](#prompt-injection-defence)). Don't put anything in
  `notes` you wouldn't send to the model provider.
- **Configured instructions and reference files**, if `directory_rules` is set.
  Reference files come from the trusted base-branch workspace, are bounded, and
  are redacted before model calls.
- **Static-analysis hints**, unless `static_analysis` is turned off
  ([static-analysis fusion](../reference/glossary.md#static-analysis-fusion)):
  redacted findings from tools run locally over the changed files. The tools
  run in a sandbox with no network, so the file text never leaves the machine
  through them. Only the redacted hints reach the model.
- **Matching specification text**, when a repository has a supported spec
  workflow and the change matches a committed spec. The
  [spec lens](../reference/glossary.md#spec-lens) sends a bounded, redacted
  selection from that spec.
- **Additional file context**, when a review or reflection needs to check a
  finding against another file. Retrieval is read-only, bounded, and redacted.
  `mid_review_retrieval` is opt-in.

With `triage_model` set, one extra call sends the same redacted per-file diffs
to the triage model, on the same provider. The triage model is a cheaper model
that skips plainly non-substantive files and ranks the rest by risk.

Review calls do not send:

- PR comments or review threads
- Commit message bodies (only the first line of each message)
- The repository name, PR number, or commit SHAs
- Unrequested repository files or the whole repository
- Committer identity or email addresses
- Any other data from the repository's git history

The [change overview](../reference/glossary.md#change-overview) (`/diagram`,
`auto_diagram`) makes up to three calls: description,
[High Impact Areas](../reference/glossary.md#high-impact-areas), and diagrams.
They use the redacted diff and, when present, the redacted stated intent. The
High Impact Areas call also sends the paths of changed files that match its risk
patterns, which are already in the diff. No new data flows; these calls only ask
for a different output. The standalone `/describe` comment sends the same inputs
again, and `/ask` sends your question with the redacted diff.

The overview comment includes an "Open full screen"
[mermaid.live](https://mermaid.live) link per diagram. The link's URL fragment
(the part after `#`) embeds that diagram's source. That source is already in the
posted comment and was built from redacted model output. Browsers decode the
fragment client-side and never send it to a server. Nothing is fetched unless a
reader clicks the link.

## Secret Redaction Before Egress

Before the diff is sent to any provider, lgtmaybe scans it for patterns that
look like secrets. It replaces the matched values with `[REDACTED]`. The same
scrub applies to:

- the context lines read from changed files;
- the stated-intent text (PR title, description, commit subjects);
- static-analysis hints;
- spec text;
- any fetched file context.

Recognised formats include:

- **Cloud and provider keys**: AWS access key IDs (`AKIA…`), OpenAI keys
  (`sk-…`), Stripe secret keys (`sk_live_…` / `sk_test_…`), and Google API keys
  (`AIza…`).
- **Source-control, chat and registry tokens**: GitHub classic tokens (`ghp_`,
  `gho_`, …), GitHub fine-grained PATs (`github_pat_…`), Slack tokens
  (`xoxb-…`), npm tokens (`npm_…`), and PyPI tokens (`pypi-…`).
- **JSON Web Tokens**: `eyJ….eyJ….…` and five-part encrypted JWEs, as whole
  tokens, since the payload can carry claims and PII.
- **Private keys**: PEM `-----BEGIN … PRIVATE KEY-----` blocks, encrypted ones
  included.
- **Generic credentials**:
    - `api_key` / `secret_key` / `access_token` / `token` assignments with a
      value of 16 or more characters;
    - quoted `password` / `passwd` / `passphrase` / `pwd` literals (the key may
      be quoted too, as in JSON);
    - `Authorization: Bearer/Basic …` headers;
    - passwords in connection-string URLs (`scheme://user:secret@host`);
    - Azure storage or Cosmos connection-string keys (`AccountKey=…` /
      `SharedAccessKey=…`).

For credential assignments only the value is replaced. The key name or URL
host stays readable, so the reviewer can still reason about the change.

Redaction is the **first** pipeline stage, before the diff is compressed or the
prompt is built, so redacted values never reach the LLM.

Redaction is a best-effort defence. Do not commit real secrets and rely on it
alone.

## Model Replies in the Log

When a model's reply can't be parsed into findings, lgtmaybe logs why. At the
default log level it records only:

- the kind of parse failure (`prose`, `malformed_json`, `not_findings`,
  `schema`, `truncated`, `empty`);
- the reply's length in characters;
- a short hash that tells you whether two runs failed on the same reply.

It never logs the content.

Set `LGTMAYBE_LOG_LEVEL=DEBUG` and the log line also carries a capped excerpt
of the reply (the first 2,000 and last 500 characters, with the gap marked).
This lets you diagnose a model that stops following the output schema without
re-running the review. The excerpt:

- passes through the same secret redaction described above. It is redacted
  **before** it is cut, so a truncated match can't slip past the redactor;
- is written to **stderr** with the other structured JSON log lines. It never
  goes to stdout (the machine-readable channel) and never to the PR;
- is model output, which can quote the diff back. That is why it is off unless
  you ask for it.

## The Repair Re-ask

When a review call's reply can't be parsed into findings, lgtmaybe sends that
reply back to the **same provider** once. It sends the output schema with it and
asks the model to re-express the reply in the required shape. This is
`repair_unparseable`, on by default.

This adds no new data. The reply is the model's own output about a diff it has
already seen. The repair call carries **no diff, no context lines, and no PR
metadata**: only the reply itself, capped and wrapped as untrusted data. It
happens at most once per failed call and never recursively.

## The Schema-less Re-run

When that reformat also fails *and* the original call carried the provider's
JSON schema, lgtmaybe re-runs that one lens once without the schema
(`retry_without_schema`, on by default). The prompt asks for JSON regardless.
Some providers force the output to fit the schema (constrained decoding). A
model whose constrained decoding produced something unreadable often answers
fine without it.

Unlike the repair, this **does re-send the diff**. It is the same request, minus
one parameter, to the same provider. So no new data leaves the process, but it
is a second full send rather than a small one. It runs at most once per lens
per batch, only after the cheaper repair has failed, and only where a schema
was sent. Set `retry_without_schema: false` to turn it off.

## Prompt-Injection Defence

lgtmaybe treats PR diff content as untrusted input throughout the pipeline. It
defends against [prompt injection](../reference/glossary.md#prompt-injection) in
depth (OWASP LLM01):

1. The diff is wrapped in explicit `DIFF_START`/`DIFF_END` delimiters and
   labelled as untrusted data. The stated-intent text gets its own
   `INTENT_START`/`INTENT_END` block with the same labelling. Every other
   untrusted block (static-analysis hints, spec text, path signals, fetched
   context) has its own delimiter family.
2. lgtmaybe **neutralises** forged delimiter markers smuggled into the diff or
   the intent text before wrapping. It does this for both marker families in
   both blocks. So a malicious PR can't close a data block early, append its own
   instructions, or fake an intent block from inside the diff.
3. The system prompt tells the model to ignore any instructions embedded in the
   diff or the intent text that try to change reviewer behaviour.
4. The model's response must validate against a strict JSON schema
   (`extra="forbid"`). lgtmaybe rejects drifted or injected fields rather than
   acting on them.

lgtmaybe does not execute any code from the PR.

## Ollama: Local or Self-Hosted

With `--provider ollama`, the diff and all other data go only to the ollama
server you name with `--api-base` (default `http://localhost:11434`). If that
server is on your machine, no data leaves it. If it is a remote host (a
Tailscale peer, a self-hosted VM), data goes only to that host. The same holds
for an `openai-compatible` server you run yourself.

lgtmaybe does not operate ollama. See ollama's own documentation for its data
handling.

## Hosted Providers: Data Handling

With any hosted provider, the redacted diff and the other inputs above are sent
over HTTPS to that provider's inference endpoint. Review each provider's data
handling policy, for example:

- **AWS Bedrock**: [AWS Bedrock data protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html)
- **Google Vertex AI**: [Vertex AI data governance](https://cloud.google.com/vertex-ai/docs/general/data-governance)
- **Azure OpenAI**: [Data, privacy, and security for Azure OpenAI](https://learn.microsoft.com/en-us/legal/cognitive-services/openai/data-privacy)
- **OpenAI**: [OpenAI API data privacy](https://openai.com/policies/api-data-privacy)
- **Anthropic**: [Anthropic privacy policy](https://www.anthropic.com/legal/privacy)
- **OpenRouter**: [OpenRouter privacy policy](https://openrouter.ai/privacy)

For z.ai and hosted `openai-compatible` endpoints such as DeepSeek, check that
provider's own policy.

## Credentials

lgtmaybe does not log or persist API keys. API-key providers receive the key
only to authenticate model calls. For Bedrock, Vertex, and keyless Azure,
lgtmaybe uses short-lived
[ambient credentials](../reference/glossary.md#ambient-credentials) and does not
write them to disk. See [Auth Model](./auth-model.md) for details.

## GitHub Token

lgtmaybe uses `GITHUB_TOKEN` to:

1. Read the PR diff and metadata through the GitHub REST API.
2. Post the review (inline comments and summary) back to the PR.

The token is never sent to an LLM provider. It needs only `contents: read` and
`pull-requests: write`. GitLab and Gitea use their own tokens, listed in
[Code-Host Auth](./auth-model.md#code-host-auth).

## Fork Pull Requests

On GitHub, lgtmaybe uses the `pull_request_target` trigger. It runs in the
context of the **base branch** (the branch the PR merges into). Code from the
fork is never checked out or executed, and the diff is fetched only through the
host's API. A malicious PR therefore can't reach the repository's secrets.

lgtmaybe never checks out or executes change-request code on any host, so a
change has nothing of its own to run inside the review. That does not protect
the pipeline definition. These all run the CI configuration from the change
itself:

- a GitLab merge request pipeline;
- a Gitea `pull_request` run;
- an Azure Pipelines pull request build.

So anyone who can push a branch can rewrite the job that holds the secrets.
Gitea 1.21 and later supports `pull_request_target`, which the supplied Gitea
workflow uses; GitLab and Azure DevOps have no equivalent. Each guide's security
section covers who can change the pipeline and how to keep the secrets out of
their reach:
[GitLab](../how-to/review-on-gitlab.md#security-who-can-change-the-pipeline),
[Gitea](../how-to/review-on-gitea.md#security-who-can-change-the-workflow) and
[Azure DevOps](../how-to/review-on-azure-devops.md#security-who-can-change-the-pipeline).
