# Development

How to run **lgtmaybe** locally, and how its tests run on your machine and in
GitHub Actions. For the contribution bar (TDD, scope) see
[CONTRIBUTING.md](CONTRIBUTING.md); for the decisions that are made, not
options, see [AGENTS.md](AGENTS.md).

## Prerequisites

- **Python 3.11+**
- **[uv](https://github.com/astral-sh/uv)**, the project's package manager and
  task runner. Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- **Docker**, only if you want to build or run the Action's container image
  locally.
- **[ollama](https://ollama.com/)**, only if you want to run a real review at
  zero cost with no cloud keys.

## Setup

```bash
uv sync --dev
```

This creates `.venv/` and installs the runtime and dev dependencies from the
locked `uv.lock`. Run everything through `uv run …` so it uses that venv (no
manual `activate` needed).

## Project Layout

Code is organised by capability, not by technical layer. The package lives
under `src/lgtmaybe/`:

| Module | Responsibility |
|---|---|
| `core/` | Frozen contracts and host-neutral helpers: `ports.py` (the hexagonal interfaces `ProviderClient`, `ReviewGateway`, `ReviewEngine`, plus the optional `Supports*` capabilities), `models.py` (pydantic data: `ReviewConfig`, `ReviewFinding`, `ReviewCategory`, …), `forge.py` (PR URL → forge/host/repo/number), `diff.py` (commentable lines and the generated-file skip filter), `diffparse.py`, `comment.py`, `findings.py`, `logging.py`. Nothing here imports an adapter. |
| `providers/` | The litellm adapter (`litellm_provider.py`), `factory.py` (strategy and factory behind `--provider`), and `credentials.py` (chain-of-responsibility auth resolution). |
| `github/` | The GitHub REST gateway (`rest_gateway.py`) and the read-only base-branch clone used for symbol lookup (`checkout.py`). |
| `gitlab/`, `gitea/` | The GitLab and Gitea gateways (`gateway.py` in each). |
| `engine/` | The review pipeline as composable stages: `redact`, `compress` (batching and hunk expansion), `injection` (wrap the diff as untrusted data), `prompt` (per-lens prompts), `parse`, `reflect`, `suppress`, `rules`, and `engine.py` (the orchestrator), plus the describe/diagram/overview, risk, spec, triage and static-analysis stages. |
| `config/` | `.lgtmaybe.yml` loading (`loader.py`) and the user-level `config` store (`store.py`). |
| `cli/` | The Click commands and runtime wiring (`__init__.py`, `commands.py`), output rendering (`render.py`), and slash-command dispatch (`slash.py`). |
| `local/` | Builds a `PRContext` from the local `git` repo: the CLI's diff source, and the local counterpart to a forge gateway. |
| `lenses/`, `rules/` | Bundled data: curated custom-lens packs (YAML) and the local semgrep rule pack. |

`tests/` mirrors this structure. `tests/fakes/` holds the in-memory fakes
(`FakeProvider`, `FakeGitHub`, `FakeEngine`) that stand in for the real
adapters, so tests never touch a live LLM or code host.

## How a Review Runs

One pipeline drives both the CLI and the Action; only the first and last stages
differ (local `git` vs a forge API):

```
fetch → redact → split + skip generated → path filters + file cap → expand hunks → batch
      → fan out one call PER LENS (concurrent) → parse → re-anchor → merge + dedupe
      → suppress → evidence gate → reflect → filter → summary → print (CLI) / post (Action)
```

The fan-out is the key non-obvious part. The prompt is composed per lens, and
the engine issues **one concurrent model call per lens per batch** (a
`ThreadPoolExecutor` over the synchronous provider port), then merges and
de-duplicates the findings before the reflection pass. The default `fast`
preset groups the ten everyday `ReviewCategory` values (security, correctness,
deprecation, tests, documentation, performance, complexity, intent, ponytail,
architecture) into **five** calls: security, correctness (with intent folded
in), code health, artefacts, and architecture. `--preset full` (or `--full`)
runs one call per category. The spec lens adds a sixth call only when a
committed spec matches the change.
[Architecture](docs/explanation/architecture.md) walks through every stage.

`ReviewConfig.categories` selects which lenses run. It is a `.lgtmaybe.yml`
setting, not a CLI flag. An explicit list replaces the preset grouping and runs
each listed lens as its own call, so `categories: [security, correctness]` means
two calls per batch. The `intent` lens reads the stated intent (PR
title/description/commit subjects on a forge; local `git log` commit subjects on
the CLI, in both branch and `--working` mode) and is skipped when nothing states
an intent, for example when there are no commits beyond the base yet.

## Running Locally

The fastest loop is the **local `review` command**: it reads your `git` diff and
prints findings. No GitHub token, no pull request, nothing posted anywhere.

```bash
# From inside a git repo, on any local branch, pushed or not:
uv run lgtmaybe review \
  --provider ollama \
  --model qwen3.6:35b \
  --api-base http://localhost:11434
```

`uv run lgtmaybe …` and `uv run python -m lgtmaybe …` are equivalent; the
latter is exactly what the Docker `ENTRYPOINT` runs.

### Reviewing an Unpushed Local Branch

`review` works entirely on **local git refs**, so the branch never has to be
pushed or have an open PR. It runs `git diff <base>...HEAD`. The three-dot form
means "everything this branch added since it forked off `<base>`": it uses the
merge-base, so changes that landed on mainline in the meantime are ignored.

`--base` chooses what mainline is. It defaults to the **remote primary branch**,
resolved as `origin/HEAD` → `origin/main` → `origin/master` → local `main` →
local `master` (→ `HEAD`, an empty comparison, when none exist — the command
then stops without a model call and exits non-zero, asking for `--base`):

```bash
uv run lgtmaybe review --base main      # diff against your LOCAL main
uv run lgtmaybe review --base develop   # forked off a different branch
uv run lgtmaybe review --base 1a2b3c4   # or an exact commit
uv run lgtmaybe review --working        # whole worktree (commits + uncommitted) vs base
uv run lgtmaybe review --uncommitted    # only uncommitted edits, vs HEAD
```

One case needs an explicit `--base`: you have an `origin` remote but haven't
fetched, so the remote-tracking refs are **stale**. Pass `--base main` to use
your local mainline, or `git fetch` first.

Useful flags for local iteration (full list: `uv run lgtmaybe review --help-all`;
plain `--help` shows only the everyday ones):

| Flag | What it does |
|---|---|
| `--base <ref>` | Diff against `<ref>` (default: the remote primary branch, resolved as above) |
| `--working` | Review the whole worktree (branch commits plus uncommitted edits) against the base |
| `--uncommitted` | Review only uncommitted working-tree edits, vs HEAD (mutually exclusive with `--working`) |
| `--json` / `--format agent` / `--format sarif` | Machine-readable output (a JSON array, correction instructions for an AI agent, or a SARIF 2.1.0 log) |
| `--min-severity medium` | Only show findings at or above a severity |
| `--max-files 10` | Cap how many changed files are reviewed |
| `--timeout 120` | Per-request timeout in seconds; raise it for slow local models |
| `--no-reflect` | Skip the self-reflection pass and keep low-confidence findings (helps weaker local models) |
| `--profile` | Print per-stage and per-call timings, tokens and cache hits at the end |

### Picking a Provider Locally

`ollama` runs fully locally at zero cost and needs no key. The others make real
API calls, so you pay for the tokens:

| Provider | What you need locally |
|---|---|
| `ollama` | Just `--api-base` (e.g. `http://localhost:11434`) |
| `openai-compatible` | `--api-base` (any OpenAI `/v1` endpoint: llama.cpp, LM Studio, vLLM, DeepSeek); a key only if the server needs one |
| `openai` / `anthropic` / `openrouter` / `zai` | `--api-key`, or the matching `*_API_KEY` env var |
| `chatgpt` | `lgtmaybe login chatgpt` once (a ChatGPT Plus or Pro subscription) |
| `bedrock` | Ambient AWS credentials (`~/.aws`), never a static key |
| `vertex` | Ambient GCP credentials (local ADC) |
| `azure` | `--api-base` (or `AZURE_API_BASE`), plus `AZURE_API_KEY` or ambient Entra credentials (`az login`) |

> The local `review` command only **prints** findings. Posting inline comments
> to a real PR happens through the GitHub Action (`comment` / `action`
> entrypoints), which needs a token and the PR event context. That path is
> exercised in CI, not from your shell.

### Inspecting Config

```bash
uv run lgtmaybe config init        # detect + verify a provider/model, write ~/.config/lgtmaybe/config.yml
uv run lgtmaybe config show        # print that user-level config
uv run lgtmaybe config get <key>   # print one value
uv run lgtmaybe config set <key> <value>
uv run lgtmaybe config path        # where the file lives (honours $XDG_CONFIG_HOME)
```

API keys are never written to this file; they stay in your environment.
`config init` makes one tiny model call to check the provider and model before
it writes; `--no-verify` skips it. With no provider or model from any layer,
`review` and `diagram` stop at once with a pointer to `config init`: there is
no built-in default.

### Debugging a Local Run

lgtmaybe logs JSON lines to stderr. Raise the level with `LGTMAYBE_LOG_LEVEL`,
and add `LITELLM_LOG=DEBUG` to see litellm's request-level detail:

```bash
LGTMAYBE_LOG_LEVEL=DEBUG LITELLM_LOG=DEBUG uv run lgtmaybe review \
  --provider ollama --model <model> --api-base http://localhost:11434
```

Things worth knowing when a local run misbehaves:

- **`⚠️ N of M review calls failed … results may be incomplete`** in the
  summary means some lens calls timed out, errored, or returned output that
  would not parse even after one repair attempt. The notice names the lenses
  lost and the most common error. If **every** call fails, the run exits
  non-zero with `review incomplete: every review call failed` rather than
  printing a false LGTM. Raise `--timeout`, pick a model that follows
  structured-output instructions, or check the debug log.
- **The fan-out makes several calls per batch**: four under the default `fast`
  preset, one per category under `full`. Up to six run at once for every
  provider (`--max-concurrency` changes that). A default ollama server
  processes one request at a time (`OLLAMA_NUM_PARALLEL=1`) and queues the
  rest, so a slow local model takes roughly `calls × per-call time`. Set
  `categories` in `.lgtmaybe.yml` to a short list while iterating.
- **Timeouts are generous by default.** ollama and openai-compatible get 1800s
  per call, multiplied by the fan-out width because queued calls start their
  clocks on send; hosted providers get 600s (openrouter 1800s). Override with
  `--timeout`.
- **`--no-reflect`** removes the extra confidence-filtering call, which helps
  when a weaker model drops valid findings during reflection. The reflection
  pass also enforces *codebase humility* (it drops findings that rest on
  assumptions about code outside the diff), so turning it off removes that guard
  too.

## Testing

### Run the Full Check Suite (What CI Runs)

These five commands are the gate. Run all of them before opening a PR:

```bash
uv lock --check                # lockfile matches pyproject (no drift)
uv run ruff check .            # lint
uv run ruff format --check .   # format (omit --check to auto-format)
uv run mypy                    # types (strict mode)
uv run pytest -q               # tests
```

### Just the Tests

```bash
uv run pytest -q                       # whole suite, quiet
uv run pytest tests/engine -q          # one directory
uv run pytest tests/engine/test_redact.py::test_name   # one test
uv run pytest -k injection             # by keyword
```

Tests are behavioural: they call functions and assert results, using the fakes
in `tests/fakes/` so nothing reaches a real LLM or code host. The layout
mirrors `src/`:

- `tests/engine/`: redaction, prompt-injection defence, prompts, parsing, caps,
  fan-out, the pipeline
- `tests/providers/`: factory, credential resolution, retries, the provider
  matrix
- `tests/github/`, `tests/gitlab/`, `tests/gitea/`: diff handling, review
  posting, each gateway
- `tests/cli/`: CLI flags, slash commands, the `action` entrypoint, e2e wiring
- `tests/core/`, `tests/local/`, `tests/config/`: diff parsing, local git
  context, config loading
- `tests/docs/` and `tests/snapshots/`: committed schema snapshots and the
  generated-docs freshness checks (see below)
- `tests/specs/`: the living-spec anchor gate. Every requirement in
  `openspec/specs/*/spec.md` has an ast-grep anchor in its `anchors.yml` that
  resolves to exactly one place in `src/` (see "Living specs" in `AGENTS.md`;
  the non-blocking PR drift warning is `scripts/check_spec_drift.py`)
- `tests/evals/`: the eval harness's scorer and fixtures (no live model)
- `tests/e2e/`: end-to-end through the real CLI, one suite against a stub
  provider and one against a live local model server. Both are marked `e2e` and
  deselected by default (see below).

### CLI Scenario e2e (tests/e2e/test_cli_scenarios.py)

Scenarios of rising complexity, from a one-file review up to an oversize diff,
path filters, line anchoring, secret redaction, the worktree modes, output
formats, and a provider that has fallen over. They drive the **real CLI**
against `tests/e2e/stub_provider.py`, an in-process OpenAI-compatible server.

The stub answers from **planted markers** in the diff it is actually sent, so
each scenario has exact ground truth:

```python
value = compute()  # @flag sev=high title=Something is wrong
```

Only the model's judgement is stubbed. Git-diff resolution, redaction,
injection wrapping, batching, parsing, re-anchoring, dedupe, reflection,
filtering and rendering are the real code. That covers the seams a unit test
never crosses, and catches whole-pipeline bugs (a file silently skipped, a
token budget ignored, an unwrapped prompt) that per-module tests cannot see.

It is hermetic (no model, network, GitHub or setup) and marked `e2e` only
because a few dozen CLI subprocesses are too slow for the per-commit gate:

```bash
uv run pytest -m e2e tests/e2e/test_cli_scenarios.py
```

### Live Provider e2e (tests/e2e/test_local_providers.py)

This suite drives the **real `lgtmaybe` CLI** against a local model server,
proving a review round-trips through each local serving stack (**ollama**,
**llama.cpp**, and **vLLM**) on a tiny qwen model. It exercises the whole path:
argument parsing, `.lgtmaybe.yml`, the local git diff, provider and credential
resolution, the litellm wire format, structured-output parsing, and rendering.
It is a **compatibility** smoke test, not a recall test; for recall use
`evals/run.py` (below).

The three backends cover both adapter routes: ollama uses litellm's native
ollama route, while llama.cpp and vLLM use the `openai-compatible` route
(`openai/` plus a custom `--api-base`).

These need a live server and a downloaded model, so they are **marked `e2e` and
deselected from the default gate** (`addopts = -m 'not e2e'`). Start the
servers, then opt in:

```bash
scripts/e2e-up.sh            # start every backend with a runner available + pull models
uv run pytest -m e2e         # run every backend the suite finds reachable
scripts/e2e-up.sh down       # stop the docker-run backends (llama.cpp, vLLM)
```

Each backend **auto-skips when its server isn't up**, so you can start just one
(`scripts/e2e-up.sh ollama`) and only that leg runs. Endpoints and model tags
are env-overridable (`LGTMAYBE_E2E_OLLAMA_BASE`, `LGTMAYBE_E2E_VLLM_MODEL`, …)
so the suite fits whatever you have running. The manually dispatched
`e2e-local.yml` workflow runs the ollama leg on a hosted runner using the same
script.

> **Context window is a launch-time setting for llama.cpp and vLLM.** With
> ollama, lgtmaybe passes `num_ctx` per request. The OpenAI-compatible servers
> fix their context at startup (llama.cpp `-c`, vLLM `--max-model-len`), and the
> client can't raise it later. So `scripts/e2e-up.sh` sets the window at launch
> (`E2E_CTX`, default 8192); raise it if you point the suite at a bigger diff.

### Regenerating Generated Files

Several files are generated and checked by the test suite, so they fail it if
they drift:

- **Schema snapshots** (`tests/snapshots/*.json`): the frozen JSON schema of
  each contract model. Regenerate after changing `core/models.py`.
- **Config reference** (`docs/reference/config.md`): generated from the models
  by `docs/generate_reference.py`. Regenerate after changing `ReviewConfig`.
- **LLM index** (`docs/llms.txt`, `docs/llms-full.txt`): generated from the
  docs by `docs/generate_llms_txt.py`. Regenerate after any docs edit.

```bash
uv run python docs/generate_reference.py   # refresh docs/reference/config.md
uv run python docs/generate_llms_txt.py    # refresh docs/llms.txt + llms-full.txt
# snapshots: write each contract model's current schema to tests/snapshots/
uv run python -c "import json; from pathlib import Path; \
from lgtmaybe.core import models as m; \
[Path(f'tests/snapshots/{n}.json').write_text(json.dumps(getattr(m,n).model_json_schema(), indent=2, sort_keys=True)+'\n') \
for n in ('ReviewFinding','ProviderResult','PRContext','ReviewConfig')]"
```

### The Deprecation Gate

`pytest` is configured (`pyproject.toml`) to treat `DeprecationWarning`,
`PendingDeprecationWarning` and `EncodingWarning` as **errors**, so using a
deprecated API (or opening a file without an explicit encoding) fails the
suite. If the warning comes from a third-party library you don't control, add a
narrow `ignore::DeprecationWarning:thatlib` to `filterwarnings` rather than
weakening the rule. `tests/test_code_quality.py` asserts the gate stays wired.

### Building and Testing the Container Image

The GitHub Action ships as a container. Build and smoke-test it locally:

```bash
docker build -t lgtmaybe:dev .
docker run --rm lgtmaybe:dev --help
```

### Previewing the Docs

```bash
uv run --group docs mkdocs serve   # http://127.0.0.1:8000
```

## Evaluating a Model (evals/)

Whether a model or setting produces *usable* reviews depends on the model, so a
small eval harness under `evals/` measures it. Each fixture is a diff with
planted bugs plus a manifest of the findings a good review should catch
(`evals/fixtures/<name>/`).

```bash
# Needs a live model, so it is NOT part of the pytest gate. Run on demand:
uv run python -m evals.run --provider ollama --model qwen3.6:35b \
  --api-base http://localhost:11434
```

It reviews each fixture and reports whether the model produced parseable output
and its **recall** against the expected findings. It also prints a
`FALSE POSITIVE:` line for any *forbidden* finding the review should not have
made. The run exits non-zero if any fixture failed to parse, pooled recall fell
below `--min-recall` (default 0.6), or a fixture wasn't **clean** (a forbidden
finding fired). Those are the three gate bars: parse, pooled recall, and clean.
`--fixture <name>` (repeatable) runs a subset; `uv run python -m evals.run
--help` lists the rest.

The scorer (`evals/scorer.py`) is pure and unit-tested (`tests/evals/`); only
the runner needs a live model. To add a fixture, add a `diff.txt` and an
`expected.json` (matching the `Fixture` schema) under `evals/fixtures/<name>/`.
A fixture may also declare `forbidden` traps: claims that must *not* appear,
typically cross-file false positives where the relevant guard lives in a file
the reviewer can't see. The `cross-file-fp` fixture is the worked example: one
genuine in-diff catch alongside three forbidden cross-file claims.

`unshown-code-fp` is its sibling, for the two shapes `cross-file-fp` cannot
express (its changed file is new, so nothing of it is unshown). Its diff is a
short edit at the bottom of a long existing module, and both traps turn on what
the reviewer was *not* shown:

- **The value of a symbol in the same file.** `SINGLE_STREAM_PROVIDERS` is an
  empty frozenset defined about 180 lines above the function that reads it, so
  a finding asserting what it *contains* ("this pins ollama to one worker") is
  false, and no amount of context padding reaches it.
- **A test that exists elsewhere.** `worker_count` arrives with no test beside
  it in the diff, while `tests/test_dispatch.py` covers it in a file the PR
  never touched. "This change is untested" is true of the diff and wrong as a
  defect.

### Secret Findings Are Scored After Redaction

Redaction is the **first** pipeline stage, so it runs on the fixtures too. A
fixture that plants `API_TOKEN = "ghp_…"` shows the model:

```
+API_TOKEN = "[REDACTED]"
```

The constant name and the inline assignment survive; the value does not. So the
finding those fixtures measure is *"a credential is held in a source constant
instead of configuration"*, a fair catch graded `medium`, and **not** *"a live
token is committed"*, which the input cannot support and which would penalise a
model for correctly declining. Their labels say so, and
`test_no_expectation_is_graded_on_a_value_the_model_never_saw` enforces it
across every fixture, because the bar moves whenever `engine/redact.py` does.

If you want a finding scored against a real value, don't bypass the redactor;
the harness exists to exercise the pipeline. Plant something it doesn't match,
and say in the label that you did.

### Separating a Reproducible Failure From an Intermittent One

A single pass says whether a fixture passed, not whether the model *reliably*
passes it. `--repeats N` runs every selected fixture N times and adds a spread
line per fixture (mean/min/max recall plus `parsed=n/N`):

```bash
uv run python -m evals.run --provider ollama --model qwen3.6:35b \
  --api-base http://localhost:11434 --repeats 5
```

A structured-output audit turns on that distinction. A model that fails to
produce parseable output on *every* repeat of a case has a reproducible defect
worth reporting upstream; one that fails on some repeats has a flakiness
problem the repair pass handles.

The per-fixture rows stay flat (N entries per fixture), so pooled recall and
precision keep their meaning and `evals/ab.py` is unaffected. Two bars get
stricter, because they apply to every row: one parse failure or one forbidden
finding in **any** repeat fails the run. That is what you want from a
false-positive gate, but it means a `--repeats 5` run is not comparable to a
single pass at the same `--min-recall`.

### Languages Other Than Python

`rust-safety`, `go-concurrency` and `typescript-web` cover the languages
`engine/astgrep.py` registers beyond Python. Each plants a bug the language
*specifically* invites (Rust's `u64` subtraction underflowing, Go's
unsynchronised map write from goroutines, JS comparing a seconds-based JWT
`exp` against a millisecond `Date.now()`) alongside the portable catches
(hardcoded token, injection, off-by-one), so recall is comparable across all
three.

Before adding another:

- The prompt's worked examples all use Python filenames (`engine/prompt.py`).
  That is not a hard dependency, but it is a real bias: if a non-Python fixture
  under-recalls, suspect the prompt before the fixture.
- The bundled semgrep pack declares only Python and JavaScript/TypeScript, so
  `--static-analysis` grounds the TypeScript fixture and is a no-op on the Rust
  and Go ones. An A/B of the fusion is only meaningful on the languages the
  pack covers.

Each ships a `repo/` corpus holding the file it adds, so `tests/evals` can check
that real ast-grep resolves a symbol in that language. Anchor Rust cases on
`function_item` or `struct_item`: `impl_item` exposes no tree-sitter `name`
field, so a bare `impl Foo` block never binds by name.

### Measuring What the Fast Preset Trades Away

The default `fast` preset covers the ten everyday lenses in five calls;
`--preset full` runs one call per lens. What that grouping costs in recall
depends on the model, so measure it on the model you actually run.
`evals/ab.py` sweeps the preset on the current tree in one command; the first
value is the baseline:

```bash
# Needs a live model. full-vs-fast recall/precision on the fixture set:
uv run python -m evals.ab --provider ollama --model qwen3.6:35b \
  --api-base http://localhost:11434 --sweep preset=full,fast --timeout 900
```

Each leg reviews every fixture through the real engine; only the preset varies
(temperature pinned to 0, fixtures fixed). The report is the pooled
recall/precision delta of `fast` against `full`. On a slow local model, expect
a `full` leg to take about `9 × per-call time` per big fixture. The harness
disables the production `max_review_seconds` ceiling, so a slow run measures
recall, not the deadline. A single `--preset full` (no comma) instead pins the
preset on both legs of a `--baseline-ref` comparison, to answer "did this change
regress the full preset?" between refs (both refs must be ≥ 0.10.0, where the
flag exists).

### Benchmarking the Recursive (RLM) Walk

`ReviewConfig.recursive` (default on) decides what happens when a single file's
diff exceeds `max_input_tokens`: walk it hunk by hunk (each hunk its own focused
call) or send the whole file in one call. `evals.ab --sweep` runs both through
the **real** engine, differing only by that flag, and reports pooled recall,
precision, and anchoring for each. "Does walking the diff raise recall?" gets a
real number instead of a hunch:

```bash
# The two RLM fixtures (rlm-bigfile, rlm-pipeline) are each a single
# multi-hunk file of ~600 tokens; a token budget below that (but above a single
# hunk) forces the over-budget split. Needs a live model.
uv run python -m evals.ab --provider ollama --model qwen3.5:4b \
  --api-base http://localhost:11434 --sweep recursive=false,true \
  --fixture rlm-bigfile --fixture rlm-pipeline --max-input-tokens 300
```

It runs both strategies once with temperature pinned to zero. The first sweep
value is the baseline, and each later value gets a pooled recall/precision delta
against it. `--categories`, `--max-input-tokens`, and the usual provider options
tune the run. The `rlm-bench` workflow runs this same command, so a local run
and CI cannot drift.

Two effects are in play, and the fixtures separate them by size:

- **Focus** (every fixture): even when the whole file fits the context window,
  splitting gives each hunk its own focused per-lens call. On small local models
  this tends to lift recall (fewer regions to attend to per call), at the cost
  of more calls and lost cross-hunk context. `rlm-bigfile` (small enough to fit
  any context window) isolates this effect.
- **Truncation avoidance** (large files only): when a file's diff exceeds the
  model's context window (`num_ctx` on ollama), the whole-file call drops the
  tail while the walk reviews every hunk. To exercise this you need a diff
  larger than the context window. The planted bugs in `rlm-bigfile`'s *tail*
  hunks (`shell=True`, N+1, the unhandled `None`) are what a truncating
  whole-file pass would miss.

Use it to confirm the walk helps on your model before trusting the default. Its
plumbing (usage accounting, the comparison record) is unit-tested in
`tests/evals/test_ab.py`; only the A/B runner needs a live model. The
`.github/workflows/rlm-bench.yml` workflow runs the sweep on demand against a
live ollama model and publishes the comparison to the run summary.

**Historical recorded result** from the earlier repeated RLM harness
(qwen3.5:4b, fixtures `rlm-bigfile` + `rlm-pipeline` pooled, token budget 300,
categories security/correctness/performance, eight samples at temperature 0.6):

| strategy | mean recall | range | spread | tokens |
|---|---|---|---|---|
| `whole` (original) | 61% | 45-82% | 36 pts | ~17.0k |
| `recursive` (RLM)  | 88% | 82-100% | 18 pts | ~40.4k |

Recursive averaged **+27 points** of recall, and its *worst* run (82%) matched
the whole-file method's *best*, so the effect is real rather than run-to-run
noise. It was also more stable (smaller spread). The diffs (~600 tokens) fit the
16k context window, so this is the *focus* ("lost in the middle") effect, **not**
truncation avoidance. The cost is about 2.4× the tokens and wall time. This was
measured on a small model, where the focus effect is largest; expect a smaller
gap on a frontier hosted model.

## Testing in GitHub Actions

### 1. `ci.yml`: The Per-PR Gate (Always Runs)

Runs on every pull request and on pushes to `main`, on Ubuntu and Windows with
Python 3.11 (Windows with autocrlf disabled and `PYTHONWARNDEFAULTENCODING=1`).
It installs uv, syncs dev dependencies, and runs the **five checks** above
(`uv lock --check`, ruff lint, ruff format check, mypy, pytest). A final `check`
job is green only when every OS leg passed, and is the single status branch
protection requires. CI makes **no** external calls and needs **no** secrets, so
it runs on forks too.

Reproduce it locally by running those five commands; nothing about them is
CI-specific.

### 2. `action-e2e.yml`: Real-Provider End-to-End Smoke Test (Opt-In)

This is **not** part of normal CI. It makes **real, billed** provider calls and
needs repo secrets, so it only runs when a PR carries the **`lgtmaybe-e2e`**
label. It builds the action image from the current checkout and has each enabled
provider review the labelled PR through `action.yml` end to end: build image →
keyless or keyed auth → fetch diff → call model → post review.

To run it:

1. Open a small throwaway PR.
2. Add the `lgtmaybe-e2e` label.
3. Each provider runs as its own matrix job (`fail-fast: false`, so one failure
   doesn't mask the rest). A **green** job means that provider authenticated,
   reached the model, and posted without erroring. The job's exit status is the
   signal; the summaries overwrite each other on the PR, which is cosmetic.

The openai, anthropic and openrouter legs are enabled. The bedrock, vertex and
azure legs are commented out in the workflow until their cloud trust is set up.
The secrets each leg needs (cloud providers are keyless via OIDC/WIF):

```
OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY   # key providers
AWS_ROLE_ARN                                            # bedrock (OIDC)
GCP_WIF_PROVIDER, GCP_SERVICE_ACCOUNT                   # vertex (WIF)
AZURE_API_BASE, AZURE_CLIENT_ID, AZURE_TENANT_ID        # azure (OIDC)
```

`ollama` is local-only (no hosted runner, no key), so it is covered by the
pytest suite (`tests/cli/test_provider_threading.py`) and the live e2e above,
not by this workflow.

### Other Workflows

- **`lgtmaybe.yml`**: dogfooding. lgtmaybe reviews its own PRs via OpenRouter,
  automatically for trusted authors and on demand via slash commands.
- **`lint-fix.yml`**: on a same-repo PR, applies ruff's autofixes and formatter and
  commits the result back to the branch. The strict gate stays in `ci.yml`.
- **`commitlint.yml`**: checks every commit in a PR against the conventional
  commit format that release-please relies on.
- **`spec-drift.yml`**: the non-blocking living-spec drift warning.
- **`audit.yml`**: `pip-audit` over the locked runtime dependencies (weekly
  cron, plus pushes and PRs that touch dependencies). A known CVE upstream
  surfaces here, not as a per-PR gate.
- **`docs.yml`**: builds (`mkdocs build --strict`) and publishes the docs site
  on pushes to `main`.
- **`e2e-local.yml`** and **`rlm-bench.yml`**: dispatch-only live-model runs
  (the local-provider e2e and the RLM benchmark).
- **`release-please.yml`**: maintains the release PR and, on release, publishes
  to PyPI (trusted publishing runs inline here so the OIDC publisher matches
  this workflow) and calls the reusable release workflows below.
- **`release.yml`**: reusable workflow called by `release-please.yml` (or run
  manually via `workflow_dispatch`) that pushes the GHCR image and moves the
  clean v-tags.
- **`homebrew.yml`**, **`windows-exe.yml`**, **`winget.yml`**: the Homebrew
  formula, the Windows executable, and the winget submission for each release.
