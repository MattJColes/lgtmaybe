---
description: Exactly what data lgtmaybe sends where — diffs only, secret redaction before egress, no code checkout, fully local with ollama.
---

# Data and Privacy

This document states precisely what data lgtmaybe sends to external services,
what is redacted before egress, which providers are fully local, and how
credentials are handled. No data flows occur beyond what is described here.

## What is sent to the LLM provider

lgtmaybe sends four grouped model calls under the default `fast` preset — the
same four on every provider, overlapping when there is more than one worker.
`full` sends one per category. The review calls fan out through one bounded pool and
are followed by a self-reflection call. Each call contains some subset of:

- The **compressed PR diff** — the unified diff of changed files, after
  generated files, lockfiles, minified assets, and vendored code have been
  stripped.
- **Surrounding context lines** — a budget-scaled number of unchanged lines
  immediately above and below each changed hunk, read from the head revision of
  the **changed files only**. This gives the model the surrounding function and
  definitions so it makes fewer false-positive findings. The amount is capped by
  `context_lines` (default 20, `0` disables it) and shrinks as the diff grows;
  this content is redacted just like the diff. It is fetched read-only via the
  GitHub API — your code is never checked out or executed.
- **PR metadata** — the repository name, PR number, base and head SHAs, and
  the list of changed file paths.
- **The PR's stated intent** — the PR title, description, and the first line of
  each commit message (on the CLI: the commit names from your local `git log`).
  This feeds the **intent lens** ("does the PR do what it says?"). It is
  redacted exactly like the diff, wrapped as untrusted data, and sent **only on
  the single lens call that carries the intent** (the correctness call under
  the default `fast` preset, or the dedicated intent lens under `full`) — drop `intent` from `categories` in
  `.lgtmaybe.yml` and it is never sent at all.
- A **system prompt** — the fixed instructions that tell the model to return
  structured JSON findings.

Nothing else is sent. lgtmaybe does not send:

- PR comments or review threads
- Commit message bodies (only the first line of each message)
- Repository contents beyond the changed files (only their hunks plus the
  surrounding context lines described above)
- Committer identity or email addresses
- Any other data from the repository's git history

The optional **description** (`/describe`, `auto_describe`) and **change
diagram** (`/diagram`, `auto_diagram`) features send exactly the same inputs —
the redacted diff and, when present, the redacted stated intent. They add no new
data flows; they only ask the model for a different output. The diagram comment
does include an "Open full screen" [mermaid.live](https://mermaid.live) link per
diagram, whose URL fragment embeds that diagram's source — but that source is the
already-public, post-redaction comment body, the fragment is decoded
client-side (browsers never send fragments to a server), and nothing is fetched
unless a reader clicks the link.

## Secret redaction before egress

Before the diff is sent to any external provider, lgtmaybe scans it for
patterns that resemble secrets and replaces the matched values with
`[REDACTED]`. The same scrub is applied to the surrounding context lines read
from changed files and to the stated-intent text (PR title, description, commit
names). Recognised formats include:

- **Cloud / provider keys** — AWS access key IDs (`AKIA…`), OpenAI keys
  (`sk-…`), Stripe secret keys (`sk_live_…`), and Google API keys (`AIza…`).
- **Source-control / chat / registry tokens** — GitHub classic tokens
  (`ghp_`, `gho_`, …), GitHub fine-grained PATs (`github_pat_…`), Slack tokens
  (`xoxb-…`), npm tokens (`npm_…`), and PyPI tokens (`pypi-…`).
- **JSON Web Tokens** — `eyJ….eyJ….…` (the whole token, since the payload
  carries claims/PII).
- **Private keys** — PEM `-----BEGIN … PRIVATE KEY-----` blocks.
- **Generic credentials** — `api_key`/`token`/`secret = "…"` assignments,
  quoted `password`/`passphrase` literals, `Authorization: Bearer/Basic …`
  headers, passwords embedded in connection-string URLs
  (`scheme://user:secret@host`), and Azure storage / Cosmos connection-string
  keys (`AccountKey=…` / `SharedAccessKey=…`).

For credential assignments only the value is replaced — the key name or URL host
stays readable so the reviewer can still reason about the change.

This happens as the **first** pipeline stage, before the diff is compressed or
the prompt is built, so redacted values never reach the LLM or appear in logs.

Redaction is a best-effort defence. Do not commit real secrets to your
repository and rely on this alone.

## Model replies in the log

When a model's reply cannot be parsed into findings, lgtmaybe logs why. At the
default log level it records only **which** parse failure it was (`prose`,
`malformed_json`, `not_findings`, `schema`, `truncated`, `empty`) and **how many
characters** the reply was — never its content.

Set `LGTMAYBE_LOG_LEVEL=DEBUG` and the log line additionally carries a capped
excerpt of the reply itself (the first 2,000 and last 500 characters, with the
gap marked). This exists so a model that stops honouring the output schema can
be diagnosed without re-running the review. The excerpt:

- is passed through the same secret redaction described above before it is
  logged, and is redacted **before** it is cut, so a truncated match cannot slip
  past the redactor;
- is written to **stderr**, alongside the other structured JSON log lines —
  never to stdout (the machine-readable channel) and never to the PR;
- is model output, which can quote the diff back. That is why it is off unless
  you ask for it.

## The repair re-ask

When a review call's reply cannot be parsed into findings, lgtmaybe sends that
reply back to the **same provider** once, with the output schema and a request
to re-express it in the required shape (`repair_unparseable`, on by default).

This adds no new data: the reply is the model's own output about a diff it has
already been sent, and the repair call carries **no diff, no context lines, and
no PR metadata** — only the reply itself, capped and wrapped as untrusted data.
It happens at most once per failed call and never recursively.

## Prompt-injection defence

PR diff content is treated as untrusted input throughout the pipeline. lgtmaybe
defends in depth (OWASP LLM01):

1. The diff is wrapped in explicit `DIFF_START`/`DIFF_END` delimiters and labelled
   as untrusted data; the stated-intent text gets its own
   `INTENT_START`/`INTENT_END` block with the same labelling.
2. Any forged delimiter markers smuggled inside the diff or the intent text are
   **neutralised** before wrapping — both marker families in both blocks — so a
   malicious PR cannot close a data block early, append its own instructions, or
   fake an intent block from inside the diff.
3. The system prompt instructs the model to ignore any instructions embedded in
   the diff or the intent text that attempt to alter reviewer behaviour.
4. The model's response must validate against a strict JSON schema
   (`extra="forbid"`); drifted or injected fields are rejected rather than acted
   on.

lgtmaybe does not execute any code from the PR.

## Ollama: fully local, zero egress

When `--provider ollama` is used, the diff and all other data are sent only to
the ollama server you specify via `--api-base`. If that server is
`http://localhost:11434`, no data leaves your machine. If it is a remote host
(Tailscale peer, self-hosted VM), data is sent only to that host.

Ollama itself is not operated by lgtmaybe. Review ollama's own documentation
for its data handling.

## Cloud providers: data handling

When using Bedrock or Vertex, the compressed and redacted diff is sent over
HTTPS to the respective cloud provider's inference endpoint. Review each
provider's data handling policies:

- **AWS Bedrock** — [AWS Bedrock data protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html)
- **Google Vertex AI** — [Vertex AI data governance](https://cloud.google.com/vertex-ai/docs/general/data-governance)
- **OpenAI** — [OpenAI API data privacy](https://openai.com/policies/api-data-privacy)
- **Anthropic** — [Anthropic usage policy](https://www.anthropic.com/legal/aup)
- **OpenRouter** — [OpenRouter privacy policy](https://openrouter.ai/privacy)

## Credentials

lgtmaybe never logs, stores, or transmits API keys. For Bedrock and Vertex,
short-lived ambient credentials are used and are never written to disk by
lgtmaybe. See [Auth Model](./auth-model.md) for details.

## GitHub token

`GITHUB_TOKEN` is used to:

1. Read the PR diff and metadata via the GitHub REST API.
2. Post the review (inline comments + summary) back to the PR.

The token is not sent to any LLM provider. It requires the minimum scopes:
`contents: read` and `pull-requests: write`.

## Fork pull requests

lgtmaybe uses the `pull_request_target` trigger, which runs in the context of
the **base branch**. PR code from the fork is never checked out or executed.
The diff is fetched exclusively through the GitHub API. This prevents a
malicious PR from gaining access to repository secrets.
