# Show HN draft

Status: drafted, awaiting Matt's edit + post. HN strips markdown, so the body is
plain paragraphs. Post from Matt's account; stay in the thread for the first few
hours to answer questions (that's most of what decides whether it ranks).

## Title

Show HN: Lgtmaybe – open-source AI PR reviewer that runs on your own model

## Body (first comment)

I built lgtmaybe because I wanted AI review on my PRs without sending code
through a third-party SaaS or putting another vendor's API key in my repo
secrets. It's a Python CLI and GitHub Action, MIT licensed: it fetches the diff
via the API (never checks out PR code), reviews it through a set of lenses
(security, correctness, tests, performance, and so on), and posts inline
comments plus a summary.

The part I care most about is auth. On AWS Bedrock, GCP Vertex, or Azure it
authenticates with GitHub OIDC, so there are no static keys anywhere. It also
runs against ollama on your own hardware for $0, or any OpenAI-compatible
endpoint if your provider isn't on the list.

Two problems turned out to be more interesting than the reviewing itself. First,
models miscount diff line numbers constantly, so every finding has to carry the
verbatim line it's flagging and the engine re-anchors it against the real diff.
A finding that can't be anchored gets demoted into the summary rather than
posted inline on the wrong line, because one comment on the wrong line costs
more trust than ten good ones earn. Second, the diff is untrusted input: a
malicious PR can try to prompt-inject the reviewer, so the diff is wrapped as
data with forged delimiters neutralised, and secrets are redacted before
anything leaves your environment.

The name is the joke I wanted before I'd written a line of it. It's a reviewer,
not a gate: it says maybe, you decide.

Repo: https://github.com/MattJColes/lgtmaybe
Docs: https://mattjcoles.github.io/lgtmaybe/

Happy to get into the line-anchoring or the injection handling if anyone's
curious.
