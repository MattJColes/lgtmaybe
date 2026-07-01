# Install the CLI

Install the `lgtmaybe` command-line tool. This is all you need to review your
local `git` diff from the terminal; the model provider is chosen at run time with
`--provider` (see [Run a local or OpenAI-compatible model](run-a-local-model.md)
or the cloud guides). To review real pull requests, you don't install anything —
use the [GitHub Action](use-as-github-action.md).

## Install

=== "pip"

    ```bash
    pip install lgtmaybe
    ```

    The cloud providers pull heavier SDKs, so they're optional extras — install
    the one you need:

    ```bash
    pip install 'lgtmaybe[bedrock]'   # AWS Bedrock
    pip install 'lgtmaybe[vertex]'    # Google Vertex AI
    pip install 'lgtmaybe[azure]'     # Azure OpenAI
    ```

=== "Homebrew"

    ```bash
    brew tap MattJColes/lgtmaybe
    brew trust MattJColes/lgtmaybe
    brew install lgtmaybe
    ```

## Verify

```bash
lgtmaybe --version
```

## Next

- Review your local changes with a local model —
  [Run a local or OpenAI-compatible model](run-a-local-model.md).
- Review pull requests automatically —
  [Use as a GitHub Action](use-as-github-action.md).
