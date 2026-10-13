---
description: Export lgtmaybe findings as a SARIF 2.1.0 log with --format sarif and upload them to GitHub code scanning or open them in a SARIF viewer.
---

# Upload Findings as SARIF

`lgtmaybe review --format sarif` prints the findings as a
[SARIF 2.1.0](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html)
log instead of a listing. SARIF is the format GitHub code scanning ingests, and
most IDEs have a SARIF viewer, so the same findings can live next to your other
static-analysis alerts rather than in pull request comments.

## Write the Log

```bash
lgtmaybe review --provider openai --model gpt-5.5 --format sarif > lgtmaybe.sarif
```

stdout carries the SARIF log and nothing else. The token footer, the `--profile`
table, and the "results may be incomplete" notice all go to stderr, so the
redirect always captures a parseable file. Every other `review` option
(`--working`, `--base`, `--min-severity` and so on) works as usual.

## What the Log Contains

| SARIF field | Comes from |
|---|---|
| `tool.driver.rules` | One rule per review lens that raised a finding (`security`, `correctness`, ...), sorted |
| `results[].ruleId` | The finding's lens; `review` when it has none |
| `results[].level` | `critical` and `high` are `error`, `medium` is `warning`, `low` and `info` are `note` |
| `results[].message.text` | The finding's title, a blank line, then its body |
| `results[].locations` | The repo-relative file, plus `region.startLine` when the line is real |
| `results[].partialFingerprints` | `lgtmaybeFingerprint/v1` and `lgtmaybeIdentity/v1`, the same ids lgtmaybe hides in posted comments |
| `results[].properties` | `severity`, plus `confidence` (0 to 10) and `suggestion` when present |
| `invocations[].executionSuccessful` | `false` when a review lens failed, so a partial log never reads as clean |

A finding whose anchor matched no changed line, or one on the old (LEFT) side of
the diff, names its file without a line. Its line number would be a guess or
would point into the old file, and a wrong-line alert misleads more than a
file-level one. The suggested fix is free text rather than an exact replacement,
so it is kept in `properties.suggestion` instead of a SARIF `fixes` entry.

The output is deterministic: the same findings give the same bytes, so a re-run
uploads an identical log.

## Upload to GitHub Code Scanning

Code scanning must be available on the repository (public repositories, or
private ones with GitHub Code Security). This workflow reviews each pull request
from a branch in the same repository and uploads the log:

```yaml
name: lgtmaybe SARIF

on:
  pull_request:

permissions:
  contents: read

jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write # upload-sarif
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0 # lgtmaybe diffs against the base branch
          persist-credentials: false
      - uses: astral-sh/setup-uv@v7
      - name: Review
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          BASE_REF: ${{ github.base_ref }}
        run: |
          uvx lgtmaybe review --provider openai --model gpt-5.5 \
            --base "origin/$BASE_REF" --format sarif > lgtmaybe.sarif
      - uses: github/codeql-action/upload-sarif@v4
        with:
          sarif_file: lgtmaybe.sarif
          category: lgtmaybe
```

`BASE_REF` goes through an environment variable rather than being expanded into
the script, which keeps workflow expressions out of the shell. `category` keeps
lgtmaybe's alerts separate from CodeQL or any other SARIF upload on the same
commit.

This runs on `pull_request` and checks out the pull request, which is fine for
branches in your own repository. A pull request from a fork gets no secrets on
`pull_request`, so the review step has no key. To review fork pull requests, use
the [GitHub Action](use-as-github-action.md) instead: it runs on
`pull_request_target`, reads the diff through the API, and never checks out PR
code.

## Open It in an IDE

Any SARIF viewer can open the file directly, for example the SARIF Viewer
extension for VS Code. Result paths are relative to `%SRCROOT%`; point the
viewer at your repository root when it asks.

## See Also

- [Install the CLI](install-the-cli.md)
- [Fix findings with an AI agent](fix-findings-with-an-ai-agent.md): the
  `--format agent` output
- [What gets reviewed](../explanation/what-gets-reviewed.md): every finding field
