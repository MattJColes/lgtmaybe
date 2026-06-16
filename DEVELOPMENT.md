# Development

How to run **lgtmaybe** locally and how its tests run — on your machine and in
GitHub Actions. For the contribution bar (TDD, scope) see
[CONTRIBUTING.md](CONTRIBUTING.md); for the decisions that are made-not-options
see [CLAUDE.md](CLAUDE.md).

## Prerequisites

- **Python 3.12+**
- **[uv](https://github.com/astral-sh/uv)** — the project's package manager and
  task runner. Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- **Docker** — only if you want to build/run the container action image locally.
- **[ollama](https://ollama.com/)** — only if you want to run a real review with
  zero cost and no cloud keys.

## Setup

```bash
uv sync --dev
```

This creates `.venv/` and installs the runtime + dev dependencies from the
locked `uv.lock`. Run everything through `uv run …` so it uses that venv (no
manual `activate` needed).

## Project layout

Code is organised by capability, not by technical layer. The package lives under
`src/lgtmaybe/`:

| Module | Responsibility |
|---|---|
| `core/` | Frozen contracts — `ports.py` (the hexagonal interfaces: `ProviderClient`, `GitHubGateway`, `ReviewEngine`), `models.py` (pydantic data: `ReviewConfig`, `ReviewFinding`, `ReviewCategory`, …), `diffparse.py`, `logging.py`. Nothing here imports from the adapters. |
| `providers/` | The litellm adapter (`litellm_provider.py`) plus the `factory.py` (strategy + factory behind `--provider`) and `credentials.py` (chain-of-responsibility auth resolution). |
| `github/` | The REST gateway (`rest_gateway.py`) and diff handling / the generated-file skip filter (`diff.py`). |
| `engine/` | The review pipeline as composable stages — `redact`, `compress` (batch + hunk expansion), `injection` (wrap the diff as untrusted data), `prompt` (per-category prompts), `parse`, `reflect`, and `engine.py` (the orchestrator). |
| `config/` | `.lgtmaybe.yml` loading + the `config` subcommand store. |
| `cli/` | Click command declarations (`commands.py`), runtime wiring (`runtime.py`, `__init__.py`), output rendering (`render.py`), and slash-command dispatch (`slash.py`). |
| `local/` | Builds a `PRContext` from the local `git` repo — the CLI's diff source, the local counterpart to the GitHub gateway. |

`tests/` mirrors this structure, and `tests/fakes/` holds the in-memory fakes
(`FakeProvider`, `FakeGitHub`, `FakeEngine`) that stand in for the real
adapters so tests never touch a live LLM or GitHub.

## How a review runs

One pipeline drives both the CLI and the Action; only the first and last stages
differ (local `git` vs the GitHub API):

```
fetch → redact → split + skip generated → file cap → expand hunks → batch
      → fan out one call PER CATEGORY (concurrent) → parse → merge + dedupe
      → reflect → filter by min_severity → summary → print (CLI) / post (Action)
```

The fan-out is the key non-obvious bit: the system prompt is composed per
`ReviewCategory` (security, correctness, deprecation, tests, documentation,
performance, complexity, intent), and
the engine issues **one concurrent model call per category per batch** (a
`ThreadPoolExecutor` over the synchronous provider port), then merges and
de-duplicates the findings before the reflection pass. `ReviewConfig.categories`
selects which lenses run (default: all eight) — it's a `.lgtmaybe.yml` knob, not a
CLI flag. Narrowing it means fewer model calls. The `intent` lens reads the PR's
stated intent (title/description/commit names on GitHub; local `git log` commit
names on the CLI, in both branch and `--working` mode) and is skipped
automatically when nothing states an intent — e.g. no commits beyond the base
branch yet.

## Running locally

The fastest loop is the **local `review` command**: it reads your `git` diff and
prints findings — no GitHub token, no pull request, nothing posted anywhere.

```bash
# From inside a git repo, on any local branch — pushed or not:
uv run lgtmaybe review \
  --provider ollama \
  --model qwen3.6:35b \
  --api-base http://localhost:11434
```

`uv run lgtmaybe …` and `uv run python -m lgtmaybe …` are equivalent — the latter
is exactly what the Docker `ENTRYPOINT` runs.

### Reviewing an unpushed local branch

`review` works entirely on **local git refs** — the branch never has to be
pushed or have an open PR. It runs `git diff <base>...HEAD`, where the three-dot
form means "everything this branch added since it forked off `<base>`" (it uses
the merge-base, so changes that landed on mainline meanwhile are ignored).

`--base` chooses what mainline is. It defaults to the **remote primary branch**,
resolved as `origin/HEAD` → `origin/main` → `origin/master` → local `main` →
local `master` (→ `HEAD`, an empty comparison, when none exist):

```bash
uv run lgtmaybe review --base main      # diff against your LOCAL main
uv run lgtmaybe review --base develop   # forked off a different branch
uv run lgtmaybe review --base 1a2b3c4   # or an exact commit
uv run lgtmaybe review --working        # whole worktree (commits + uncommitted) vs base
uv run lgtmaybe review --uncommitted    # only uncommitted edits, vs HEAD
```

One case needs an explicit `--base`: you have an `origin` remote but haven't
fetched — the remote-tracking refs can be **stale**; pass `--base main` to use
your local mainline (or just `git fetch` first).

Useful flags for local iteration (full list: `uv run lgtmaybe review --help`):

| Flag | What it does |
|---|---|
| `--base <ref>` | Diff against `<ref>` (default: remote primary branch — `origin/HEAD` → `origin/main` → `origin/master` → `main` → `master`) |
| `--working` | Review the whole worktree (branch commits + uncommitted edits) against the base |
| `--uncommitted` | Review only uncommitted working-tree edits, vs HEAD (mutually exclusive with `--working`) |
| `--json` / `--format agent` | Machine-readable output (JSON array, or correction instructions for an AI agent) |
| `--min-severity medium` | Only surface findings at/above a severity |
| `--max-files 10` | Cap how many changed files are reviewed |
| `--timeout 120` | Per-request timeout — raise it for slow local models |
| `--no-reflect` | Keep low-confidence findings (helps weaker local models) |

### Picking a provider locally

`ollama` runs fully local at zero cost and needs no key. The others make real
API calls, so you pay for the token usage:

| Provider | What you need locally |
|---|---|
| `ollama` | Just `--api-base` (e.g. `http://localhost:11434`) — fully local |
| `openai` / `anthropic` / `openrouter` | `--api-key` or the matching `*_API_KEY` env var |
| `bedrock` | Ambient AWS creds (`~/.aws`) — never a static key |
| `vertex` | Ambient GCP creds (local ADC) |
| `azure` | `AZURE_API_KEY` + `--api-base`, or ambient Azure AD creds |

> The local `review` command only **prints** findings. Posting inline comments
> to a real PR happens through the GitHub Action (`comment` / `action`
> entrypoints), which needs a GitHub token and the PR event context — that is
> exercised end-to-end in CI, not from your shell.

### Inspecting config

```bash
uv run lgtmaybe config init     # write a starter .lgtmaybe.yml
uv run lgtmaybe config show     # print the resolved config
uv run lgtmaybe config get <key>
```

### Debugging a local run

litellm prints a generic banner (`Give Feedback / Get Help: …`) on **any**
provider hiccup, and the real error is the line right after it. To see what
actually happened, turn on litellm's debug logging:

```bash
LITELLM_LOG=DEBUG uv run lgtmaybe review --provider ollama --model <model> \
  --api-base http://localhost:11434
```

A few things worth knowing when a local run misbehaves:

- **`👍 LGTM! · 0 findings` on code you know is buggy** usually means the model's
  output didn't parse as the required JSON findings array (small/instruct models
  often wrap it in prose or reasoning), so each category parsed to empty. A run
  can therefore "succeed" with zero findings even though every call struggled —
  check the debug log, and prefer a model that follows the structured-output
  instruction. `parse.py` tolerates fenced JSON but not arbitrary prose.
- **Fan-out makes N calls** (one per category — all eight by default, seven when
  nothing states an intent). They run
  **concurrently for cloud** providers but **serially for ollama** (a single
  ollama instance serves one request at a time, so concurrency would only cause
  timeouts). A slow local model therefore takes ≈ `categories × per-call time` —
  narrow `categories` in `.lgtmaybe.yml` while iterating. ollama gets a **300 s
  default timeout** automatically; raise it with `--timeout` for very large models.
- **`⚠️ review incomplete` (non-zero exit)** means every category call errored or
  returned unparseable output — lgtmaybe reports this rather than a false LGTM.
  Raise `--timeout`, pick a more reliable model, or check `LITELLM_LOG=DEBUG`.
- **`--no-reflect`** removes the extra confidence-filtering call — useful when a
  weaker model drops valid findings during reflection, and one fewer call to wait
  on.

## Testing

### Run the full check suite (what CI runs)

These four commands are the gate. Run all of them before opening a PR:

```bash
uv lock --check          # lockfile matches pyproject (no drift)
uv run ruff check .      # lint
uv run ruff format --check .   # format (omit --check to auto-format)
uv run mypy              # types (strict mode)
uv run pytest -q         # tests
```

### Just the tests

```bash
uv run pytest -q                       # whole suite, quiet
uv run pytest tests/engine -q          # one directory
uv run pytest tests/engine/test_redact.py::test_name   # one test
uv run pytest -k injection             # by keyword
```

Tests are behavioural — they call functions and assert results, using the fakes
in `tests/fakes/` (fake provider, fake GitHub gateway) so nothing reaches a real
LLM or the GitHub API. Test layout mirrors `src/`:

- `tests/engine/` — redaction, prompt-injection defense, prompt, parse, caps, fan-out, the pipeline
- `tests/providers/` — factory, credential resolution, retries, the provider matrix
- `tests/github/` — diff handling, review posting, the REST gateway
- `tests/cli/` — CLI flags, slash commands, the `action` entrypoint, e2e wiring
- `tests/core/`, `tests/local/` — diff parsing, local git context
- `tests/docs/` + `tests/snapshots/` — committed schema snapshots and the
  generated-reference freshness check (see below)

### Regenerating generated artifacts

Two things are generated from the pydantic models and checked in CI, so they
fail the suite if they drift after you change `core/models.py`:

- **Schema snapshots** (`tests/snapshots/*.json`) — the frozen JSON schema of each
  contract model.
- **Config reference** (`docs/reference/config.md`) — generated by
  `docs/generate_reference.py`.

Regenerate both after a contract change:

```bash
uv run python docs/generate_reference.py        # refresh docs/reference/config.md
# snapshots: update the committed JSON to the model's current schema, e.g.
uv run python -c "import json; from pathlib import Path; \
from lgtmaybe.core import models as m; \
[Path(f'tests/snapshots/{n}.json').write_text(json.dumps(getattr(m,n).model_json_schema(), indent=2, sort_keys=True)+'\n') \
for n in ('ReviewFinding','ProviderResult','PRContext','ReviewConfig')]"
```

### The deprecation gate

`pytest` is configured (`pyproject.toml`) to treat `DeprecationWarning` and
`PendingDeprecationWarning` as **errors** — using a deprecated API fails the
suite. If the warning comes from a third-party library you don't control, add a
narrow `ignore::DeprecationWarning:thatlib` to `filterwarnings` rather than
weakening the rule. `tests/test_code_quality.py` asserts the gate stays wired.

### Building and testing the container image

The GitHub Action ships as a container. Build and smoke-test it locally:

```bash
docker build -t lgtmaybe:dev .
docker run --rm lgtmaybe:dev --help
```

### Previewing the docs

```bash
uv run --group docs mkdocs serve   # http://127.0.0.1:8000
```

## Evaluating a model (evals/)

Whether a given model/setting actually produces *usable* reviews depends on the
model — so there's a small eval harness under `evals/` to measure it, rather than
finding out by hand. Each fixture is a diff with planted bugs plus a manifest of
the findings a good review should catch (`evals/fixtures/<name>/`).

```bash
# Needs a live model — NOT part of the pytest gate. Run on demand:
uv run python -m evals.run --provider ollama --model qwen3.6:35b \
  --api-base http://localhost:11434
```

It reviews each fixture and reports, per fixture, whether the model produced
parseable output and its **recall** against the expected findings, then exits
non-zero if any fixture failed to parse or fell below `--min-recall` (default
0.6) — so it can gate a model or prompt change.

The scorer (`evals/scorer.py`) is pure and unit-tested (`tests/evals/`); only the
runner needs a live model. To add a fixture, drop a `diff.txt` and an
`expected.json` (matching the `Fixture` schema) under `evals/fixtures/<name>/`.

### Benchmarking the recursive (RLM) walk

`ReviewConfig.recursive` (default on) decides what happens when a single file's
diff exceeds `max_input_tokens`: walk it hunk-by-hunk (each hunk its own focused
call) or send the whole file in one call. `evals/rlm.py` is an on-demand A/B
benchmark that runs both through the **real** engine — they differ only by that
one flag — and reports recall + token usage for each, so "does walking the diff
actually raise recall?" gets a real number instead of a hunch:

```bash
# Defaults to both RLM fixtures (rlm-bigfile, rlm-pipeline) — each a single
# multi-hunk file ~600 tokens; --budget below that (but above a single hunk)
# forces the over-budget split. --repeats samples the spread. Needs a live model.
uv run python -m evals.rlm --provider ollama --model qwen3.5:4b \
  --api-base http://localhost:11434 --repeats 8
```

It runs both strategies through the real engine `--repeats` times (recall pooled
across the fixtures per run) and prints each strategy's recall **mean / min / max
/ spread** plus mean token cost and a one-line verdict (e.g. *"recursive recall
+25% (mean) at 1.4x tokens"*). The spread is the point — at temperature > 0 a
single run is noisy, so a wide `--repeats` tells you whether the gap is real or
sampling luck. `--only whole|recursive` runs one strategy (the CI matrix uses it
to parallelise the two legs); `--categories` / `--budget` / `--num-ctx` /
`--temperature` tune it. This is the **same command** the `rlm-bench` workflow
runs, so a local run and CI can't drift.

Two distinct effects are in play, and the benchmark separates them by fixture
size:

- **Focus** (every fixture): even when the whole file fits the model's context,
  splitting gives each hunk its own focused per-lens call. On small local models
  this tends to lift recall — fewer regions to attend to per call — at the cost
  of more calls and lost cross-hunk context. `rlm-bigfile` (small, fits any
  context window) isolates this effect.
- **Truncation-avoidance** (large files only): when a file's diff genuinely
  exceeds the model's context window (`num_ctx` on ollama), the whole-file call
  drops the tail entirely while the walk reviews every hunk. To exercise this you
  need a diff larger than the context window — the planted bugs in `rlm-bigfile`'s
  *tail* hunks (`shell=True`, N+1, the unhandled `None`) are what a truncating
  whole-file pass would miss.

Use it to confirm the walk helps on your model before trusting the default. Its
plumbing (usage accounting, the comparison record) is unit-tested in
`tests/evals/test_rlm.py`; only the A/B runner needs a live model.

The `.github/workflows/rlm-bench.yml` workflow runs this A/B as two separate
matrix tasks (`whole` vs `recursive`) against a live ollama model and publishes
each leg's recall to the run summary — on demand (`workflow_dispatch`) or when the
RLM code/fixture changes.

**Recorded result** (qwen3.5:4b, `rlm-bigfile`, `--max-input-tokens 300`,
`--categories security,correctness,performance`, single run, temperature 0.6):

| strategy | recall | notes |
|---|---|---|
| `whole` (original) | 67% (4/6) | missed both bugs in the file's tail hunk |
| `recursive` (RLM)  | 100% (6/6) | caught the tail bugs the whole-file pass dropped |

The diff (~600 tokens) fit the 16k context window, so the gap is the *focus*
effect, not truncation. One non-deterministic run on one fixture — directional,
not a guarantee; re-run to confirm on your own model.

## Testing in GitHub Actions

Two distinct CI workflows cover two different things.

### 1. `ci.yml` — the per-PR gate (always runs)

Runs on every pull request and on pushes to `main`. It installs uv, syncs dev
deps, and runs the **exact five checks** listed above (`uv lock --check`, ruff
lint, ruff format check, mypy, pytest). This is the gate every PR must pass —
green CI + a test is the merge bar. It makes **no** external calls and needs **no**
secrets, so it runs on forks too.

Reproduce it locally by running those five commands; there is nothing
CI-specific about them.

### 2. `action-e2e.yml` — real-provider end-to-end smoke test (opt-in)

This is **not** part of normal CI. It makes **real, billed** provider calls and
needs repo secrets, so it only runs when a PR carries the **`lgtmaybe-e2e`**
label. It builds the action image from the current checkout and has every hosted
provider review the labelled PR through `action.yml` end to end — build image →
keyless/keyed auth → fetch diff → call model → post review.

To run it:

1. Open a small throwaway PR.
2. Add the `lgtmaybe-e2e` label.
3. Each provider runs as its own matrix job (`fail-fast: false`, so one failure
   doesn't mask the rest). A **green** job means that provider authenticated,
   reached the model, and posted without erroring — the job's exit status is the
   real signal (the summaries overwrite each other on the PR, which is cosmetic).

It requires these repo secrets (cloud providers are keyless via OIDC/WIF):

```
OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY   # key providers
AWS_ROLE_ARN                                            # bedrock (OIDC)
GCP_WIF_PROVIDER, GCP_SERVICE_ACCOUNT                   # vertex (WIF)
AZURE_API_BASE, AZURE_CLIENT_ID, AZURE_TENANT_ID        # azure (OIDC)
```

`ollama` is local-only (no hosted runner, no key), so it is covered by the
pytest suite (`tests/cli/test_provider_threading.py`), not by this workflow.

### Other workflows

- **`audit.yml`** — `pip-audit` over the locked runtime deps (weekly cron +
  on dependency-touching changes). A known CVE upstream surfaces here, not as a
  per-PR gate.
- **`docs.yml`** — builds/publishes the docs site.
- **`release.yml`** — on a `v*.*.*` tag: PyPI trusted publishing + GHCR image push.
