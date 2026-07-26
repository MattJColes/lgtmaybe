---
description: What lgtmaybe reviews and how it bounds the work — only changed lines, padded with surrounding context, never your whole repo.
---

# What gets reviewed

This page explains what lgtmaybe looks at, how it bounds the work, and what the
output looks like — on a GitHub PR and on the command line.

## What it looks at

lgtmaybe reviews the **diff of a pull request** — the lines the PR adds or
changes — not the whole repository. It fetches that diff from the GitHub REST
API and **never checks out or executes your code**, so a malicious PR can't run
anything in the reviewer's environment. The diff is treated as untrusted input
throughout, including against prompt-injection attempts hidden in PR text.

To review changes in context rather than in isolation, lgtmaybe also pads each
changed hunk with a few **surrounding lines** read from the head revision of the
changed file. The model uses these to understand the change — the enclosing
function, nearby definitions — but only ever comments on the changed lines. How
many lines are added is budget-scaled and capped by `context_lines` (default 20,
`0` disables it). This content is fetched read-only via the API and redacted
like the diff.

Before the diff reaches the model it is cleaned:

- **Non-reviewable files are skipped** — lockfiles, minified/bundled assets,
  vendored directories, generated LLM-index corpora (`llms.txt` / `llms-full.txt`),
  and binaries. Reviewing them is noise and wastes tokens.
- **Secrets are redacted** — anything that looks like a key or token is stripped
  from the diff before it leaves your environment for the provider.

## Correctness & logic

The substance of a change, not just style. The model is prompted to actively
hunt the bugs a change introduces and grade them by impact:

- **Null / None dereferences** — a value that can be empty used without a guard.
- **Off-by-one & boundary errors** — `<` vs `<=`, fencepost mistakes, empty- and
  single-element edge cases.
- **Mismatched or inverted ranges** — `start`/`end` swapped, a lower bound above
  its upper bound.
- **Unhandled error / exception paths** — failures silently swallowed or state
  left half-updated.
- **Incorrect conditionals** — inverted booleans, `and`/`or` mix-ups, missing
  branches.
- **Resource leaks & ordering** — handles or locks not released, use-after-close,
  bad concurrent sequencing.
- **Races & concurrency** — check-then-act (TOCTOU), shared mutable state without
  synchronisation, coroutines called without `await`, blocking calls in async paths.
- **Numeric and date/time bugs** — overflow, float equality, division by zero,
  money in binary floats; timezone-naive datetimes, epoch-unit confusion, DST.
- **Aliasing & mutation** — mutable default arguments, mutating a collection while
  iterating it, sharing a mutable value the caller still owns.

=== "On a GitHub PR"

    ![An inline lgtmaybe review comment flagging a [HIGH] possible None dereference, where get_user can return None but .email is accessed without a guard](../assets/review-correctness.png){ width="660" }

=== "On the CLI"

    ![The lgtmaybe CLI printing a [HIGH] None-dereference finding for demo/orders.py](../assets/cli-correctness.png){ width="660" }

## Security review

Security findings are first-class. The model is prompted with an OWASP-aligned
checklist and told to grade what it finds `high` or `critical` and name the
vulnerability class in the title. It actively looks for:

- **Injection** — SQL/NoSQL, OS command, and template/LDAP injection.
- **Cross-site scripting (XSS)** — unescaped user input rendered into HTML/JS.
- **CSRF & open redirect** — unprotected state-changing endpoints, user-controlled
  redirect targets.
- **Hardcoded secrets** — keys, tokens, passwords, or private keys in the diff.
- **Broken authn / authz** — missing permission checks, IDOR, auth bypass, and JWT
  or session pitfalls (unverified signatures, `alg` confusion, missing expiry).
- **Path traversal / unsafe file access** — user input in file paths, `../`,
  zip-slip extraction — plus unrestricted file uploads.
- **SSRF** — server-side fetches of user-controlled URLs without allow-listing.
- **Insecure deserialization & unsafe eval** — `pickle`/`yaml.load`/`eval` on
  untrusted data, and XML parsed with external entities enabled (XXE).
- **Mass assignment / over-posting** — request bodies bound straight onto models.
- **Weak cryptography** — MD5/SHA1 for passwords, ECB mode, disabled TLS
  verification, predictable randomness for security tokens.
- **Sensitive-data exposure** — secrets or PII in logs, error responses, or
  analytics: passwords, API keys, tokens/session IDs, SSNs, or payment-card data.
- **CI / IaC misconfiguration** — untrusted input interpolated into workflow `run:`
  steps, third-party actions not pinned to a SHA, overly broad IAM policies,
  public buckets, privileged containers, secrets echoed into build logs.
- **Resource / DoS safety** — missing timeouts, unbounded loops or allocations,
  regexes vulnerable to catastrophic backtracking (ReDoS).

=== "On a GitHub PR"

    ![An inline lgtmaybe review comment flagging a [CRITICAL] SQL injection vulnerability in a find_user function, explaining the unsafe string concatenation and suggesting a parameterized query](../assets/review-sql-injection.png){ width="660" }

=== "On the CLI"

    ![The lgtmaybe CLI printing a [CRITICAL] SQL injection finding for demo/db_queries.py](../assets/cli-security.png){ width="660" }

This shapes *what* the reviewer flags. It is separate from how lgtmaybe protects
**itself** from a malicious PR — see
[Data and Privacy](data-and-privacy.md) for secret redaction and prompt-injection
defence.

## Deprecation & dependency health

Beyond bugs and vulnerabilities, the reviewer also flags **factually outdated**
code when the diff shows it — these are objective, not stylistic:

- deprecated language/framework APIs (with the modern replacement suggested when
  known),
- targeting an end-of-life runtime or language version,
- adding or pinning an end-of-life / abandoned dependency,
- pinning a dependency to a version with a known security advisory, and
- a new dependency whose name looks like a typosquat of a popular package, or
  whose license conflicts with the project's.

The reviewer only raises these when the diff itself shows the change; it does not
speculate about code it cannot see.

=== "On a GitHub PR"

    ![An inline lgtmaybe review comment flagging a [MEDIUM] deprecated datetime.utcnow() call and suggesting datetime.now(timezone.utc)](../assets/review-deprecation.png){ width="660" }

=== "On the CLI"

    ![The lgtmaybe CLI printing a [MEDIUM] deprecated-API finding for demo/scheduler.py](../assets/cli-deprecation.png){ width="660" }

## Test coverage & documentation

Two lighter-weight checks round out a review:

- **Missing or weak tests** — when the diff adds a new function, branch, or error
  case with no accompanying test, the reviewer raises a `low`/`medium` finding and
  puts a concrete, runnable test in the finding's `suggestion` field, matching
  the project's existing test idiom. Tests added in the diff that don't really
  test — assertion-free, over-mocked until only the mock is exercised, or flaky
  (sleep-based waits, wall-clock or ordering dependence) — are flagged too.
  Renames, comments, and trivial formatting changes are left alone.
- **Documentation gaps and stale docs** — public/exported surfaces added without
  a docstring, or a name or signature that contradicts what the code does, are
  flagged at `info`/`low`; a docstring or comment the change just made wrong is
  flagged up to `medium` (a comment that lies is worse than no comment). This is
  deliberately restrained: private helpers and self-evident code are not nagged
  about, so well-named code is left to document itself.

A missing test — note the runnable test dropped into the suggestion:

=== "On a GitHub PR"

    ![An inline lgtmaybe review comment flagging a [LOW] new branch added without a test, with a runnable pytest suggestion](../assets/review-tests.png){ width="660" }

=== "On the CLI"

    ![The lgtmaybe CLI printing a [LOW] missing-test finding for demo/discount.py](../assets/cli-tests.png){ width="660" }

A documentation gap on a new public function:

=== "On a GitHub PR"

    ![An inline lgtmaybe review comment flagging an [INFO] public function missing a docstring, with a suggested docstring](../assets/review-documentation.png){ width="660" }

=== "On the CLI"

    ![The lgtmaybe CLI printing an [INFO] missing-docstring finding for demo/client.py](../assets/cli-documentation.png){ width="660" }

## Performance

The reviewer also watches for performance regressions the change introduces,
graded by impact (`low` up to `high` when the cost scales with input size or sits
in a hot path):

- **N+1 queries / calls in a loop** — a query, request, or other expensive call
  issued once per iteration that could be batched or hoisted out.
- **Inefficient algorithms** — accidentally quadratic (`O(n²)`) work where linear
  is feasible, or a linear scan where a set/dict lookup would do.
- **Redundant computation** — recomputing the same value inside a loop instead of
  hoisting or memoising it.
- **Unnecessary allocations & copies** — building large intermediates or copying
  big buffers on a hot path when streaming or in-place work suffices.
- **Blocking I/O on a hot path** — synchronous I/O, sleeps, or lock contention
  where non-blocking handling is expected.
- **Unbounded / over-fetching queries** — loading whole tables into memory or
  missing pagination/limits.
- **Unbounded growth & leaks** — caches without eviction, listeners or
  subscriptions never removed, queues that only grow.

It sticks to changes the diff actually shows and avoids micro-optimisations with
no measurable impact.

=== "On a GitHub PR"

    ![An inline lgtmaybe review comment flagging a [HIGH] N+1 query inside a loop, suggesting a single batched query](../assets/review-performance.png){ width="660" }

=== "On the CLI"

    ![The lgtmaybe CLI printing a [HIGH] N+1-query finding for demo/reports.py](../assets/cli-performance.png){ width="660" }

## Complexity

A lighter, restrained lens that flags code harder to read, test, or maintain than
it needs to be (`info`/`medium`), preferring a concrete simplification in the
`suggestion` field:

- **High cyclomatic complexity / deep nesting** — many branches or deeply nested
  conditionals and loops that would read better with early returns.
- **Over-long, low-cohesion functions** — a function doing several unrelated
  things that should be split apart.
- **Duplicated logic** — non-trivial logic repeated in the diff that should be
  extracted into a shared helper.
- **Excessive parameters / boolean-flag arguments**, **convoluted expressions**,
  and **dead / unreachable code**.

Like the documentation lens, it stays quiet on self-evident, already-simple code.

=== "On a GitHub PR"

    ![An inline lgtmaybe review comment flagging a [MEDIUM] deeply nested conditional and suggesting guard clauses](../assets/review-complexity.png){ width="660" }

=== "On the CLI"

    ![The lgtmaybe CLI printing a [MEDIUM] deep-nesting finding for demo/router.py](../assets/cli-complexity.png){ width="660" }

## Intent — does the PR do what it says?

The intent lens compares the diff against the PR's **stated intent** and flags
mismatches at `medium`, or `high` when the unexplained change is
security-relevant:

- **Out-of-scope changes** — a hunk unrelated to the stated intent, e.g. a "fix
  typo" PR that also touches auth logic, CI workflows, dependency pins, or
  permissions. Smuggled security-relevant changes are the highest-value catch.
- **Contradictions** — the code does the opposite of, or something materially
  different from, what the title or commits claim.
- **Unfulfilled intent** — the PR promises behaviour the diff never implements.

Where the stated intent comes from:

- **On a GitHub PR** — the PR title, description, and the first line of each
  commit message, fetched via the API.
- **On the CLI** — the commit names from your local `git log` against the
  remote primary branch, so the lens works without GitHub — in `--working`
  mode too. With no commits beyond the base yet, nothing states an intent and
  the lens is skipped.

The intent text is attacker-controlled on a fork PR, so it is treated exactly
like the diff: secrets are redacted, it is wrapped as untrusted data with
neutralised delimiters, and the model is told never to follow instructions
inside it. Only the intent lens's model call ever carries it. When a PR states
no intent at all, the lens is skipped instead of burning a model call.

## Ponytail — the laziest senior dev in the room

The best code is the code you never wrote. Inspired by the
[Ponytail](https://github.com/DietrichGebert/ponytail) skill, this lens reviews
new code with a senior engineer's reflex to **not** add code, flagging what
needn't exist at all (graded `info` to `medium`, and deliberately restrained):

- **Needless code (YAGNI)** — speculative generality, "just in case" parameters,
  an abstraction with a single caller, or scaffolding for a future that isn't here.
- **Reinventing the standard library** — hand-rolled code a built-in, the standard
  library, or an already-imported dependency does directly.
- **Could be far shorter** — several lines doing what one clear expression would.
- **Premature configurability** — flags, hooks, or options no caller uses yet.

It prefers deleting or collapsing code over adding to it and puts the smaller
replacement in the suggestion. It is distinct from the complexity lens (which asks
"is this code hard to follow?"); Ponytail asks "should this code exist at all?"

## How the scope is bounded

Every run is bounded so a large PR can't run away on latency. All of these are
configurable in `.lgtmaybe.yml` (see
[Configure .lgtmaybe.yml](../how-to/configure-lgtmaybe-yml.md)):

| Knob | Default | Effect |
|---|---|---|
| `preset` | `fast` | `fast` uses four calls when parallelism is available, three with one worker; `full` restores tests/documentation and runs one call per lens. |
| `max_files` | 50 | Reviews the top-N changed files; posts a "reviewed top N of M" notice if there are more. |
| `max_input_tokens` | 100,000 | Batches the diff so each model call stays within budget. |
| `max_concurrency` | 8 cloud / 1 ollama, openai-compatible | Concurrent model calls across the whole fan-out (all batches share one pool). |
| `max_review_seconds` | 3600 | Soft wall-clock ceiling: past it, queued calls are skipped and the review posts partial results with a notice. `0` disables. |
| `categories` | all nine | Which review lenses to run; an explicit list overrides the preset grouping and runs those lenses one call each. |
| `context_lines` | 20 | Ceiling on surrounding lines added around each hunk; the budget may use fewer. `0` disables context expansion. |
| `min_severity` | `low` | Drops findings below the chosen floor (`info` → `low` → `medium` → `high` → `critical`); `low` keeps everything except pure-`info` narration. |
| `include_paths` / `exclude_paths` | — | Glob filters to focus the review. |

> These bound a **single run**, not the number of runs. On a public repo, anyone
> who can open a PR or comment can trigger a run, and each run calls your chosen
> LLM provider — see the cost disclaimer in
> [Use as a GitHub Action](../how-to/use-as-github-action.md).

## What a finding contains

Findings are structured data, not prose, so they render identically everywhere.
Each finding has:

| Field | Meaning |
|---|---|
| `path` | File the comment attaches to |
| `line` | Line in the diff |
| `side` | `RIGHT` (added/changed) or `LEFT` (removed) |
| `severity` | `info` / `low` / `medium` / `high` / `critical` |
| `title` | One-line summary |
| `body` | The explanation |
| `suggestion` | Optional suggested replacement code |

The nine review categories are security, correctness, deprecation, tests,
documentation, performance, complexity, intent, and ponytail. The default
`fast` preset covers seven of them in four concurrent calls when parallelism is
available: security, correctness flow/intent, correctness state/lifecycle, and
merged code health (performance/complexity/ponytail/deprecation). With one
worker, correctness stays combined and the preset uses three calls.
`preset: full` restores tests and documentation and runs each category as its
own focused call with a worked example of its own finding type. Their findings
are merged and de-duplicated. A
self-reflection pass then runs over the merged set and drops low-confidence
findings, so the model's first guesses are filtered before anything is posted.

The reviewer only ever sees the diff and a little surrounding context — a *slice*
of the codebase, not the whole thing. So when a concern depends on code it can't
see (a guard, a base class, an idempotency check that may live in an unshown
file), it hedges and lowers the severity rather than asserting that the thing is
missing, and the self-reflection pass drops findings that rest on such unseen-code
assumptions. Genuine gaps in the diff itself — a changed path with no test, a new
public surface left undocumented — are explicitly exempt and still raised.

## What the response looks like

### On a GitHub pull request

lgtmaybe posts **one review** containing:

- an **inline comment** on the exact changed line for each finding, and
- a **summary comment** that names the model used.

Each finding lands on the line that triggered it, with its severity in the title,
the explanation in the body, and — where the fix is clear — a suggested change you
can commit straight from the PR:

![An inline lgtmaybe review comment flagging a [MEDIUM] server-side request forgery (SSRF) risk where a user_id is concatenated into a URL, with a suggested validation fix](../assets/review-ssrf.png){ width="660" }

![An inline lgtmaybe review comment flagging a [CRITICAL] command injection vulnerability in an archive function using subprocess with shell=True, with a suggested fix that avoids the shell](../assets/review-command-injection.png){ width="660" }

The summary carries a hidden marker (`<!-- lgtmaybe -->`), so re-running on the
same PR **updates** the existing review instead of creating duplicates.

### Resolving conversations once they're fixed

Each inline comment also carries a hidden per-finding fingerprint. When you push
a fix and lgtmaybe runs again, it looks at its own open conversations: if a
finding it raised is **no longer produced** *and* GitHub marks that thread
**outdated** (the code under it changed), lgtmaybe treats it as fixed — it posts
a short `✅ Looks resolved.` reply and resolves the conversation. Both conditions
must hold, so a thread is never collapsed just because the lines around it
shifted, or because a single run happened not to re-flag it without the code
changing.

This is on by default. To leave conversations for manual resolution, set
`resolve_fixed: false` in `.lgtmaybe.yml` (or the Action's `resolve_fixed` input).
Resolving a thread uses GitHub's GraphQL API; the workflow's default
`GITHUB_TOKEN` (with `pull-requests: write`, already needed to post the review)
is sufficient. The step is best-effort — if it can't run, the review itself still
posts normally.

When a PR is clean (no findings, and every file was within the caps), the summary
is a simple:

```
👍 LGTM!

0 findings · provider anthropic · model claude-sonnet-4-6
```

If the file cap kicked in, the summary says so (e.g. "Reviewed the top 50 of 120
changed files"). lgtmaybe never fails silently — any error is surfaced back to
the PR as a short comment.

### On the command line

`lgtmaybe review` runs the same pipeline over your local `git` diff and prints
the findings — it posts nothing and needs no GitHub token. By default it diffs
the current branch against the remote primary branch (`origin/HEAD`, falling
back to `origin/main`/`origin/master`, then a local `main`/`master`).
`--working` reviews the whole worktree — branch commits plus uncommitted edits —
against that same base. `--uncommitted` reviews only the uncommitted edits
against HEAD, and `--base <ref>` picks a different base. The default output is a
readable listing followed by the summary line:

```console
$ lgtmaybe review --provider ollama --model qwen3.6:27b --api-base http://localhost:11434
src/app.py:2  [MEDIUM] Import order
  sys should be sorted before os

1 finding · provider ollama · model qwen3.6:27b
```

![The lgtmaybe review command running in a terminal, printing a [MEDIUM] import-order finding with its file and line, then a summary line naming the model](../assets/cli-example.png){ width="660" }

`--format` selects the output. `--json` is shorthand for `--format json`, which
prints the findings as a JSON array so the same structured data can be piped into
other tooling:

```console
$ lgtmaybe review --provider ollama --model qwen3.6:27b --api-base http://localhost:11434 --json
[{"path": "src/app.py", "line": 2, "side": "RIGHT", "severity": "medium",
  "title": "Import order", "body": "sys should be sorted before os",
  "suggestion": null}]
```

`--format agent` turns the findings into plain correction instructions an AI
coding agent can read and apply — a local review-and-fix loop. See
[Fix findings with an AI agent](../how-to/fix-findings-with-an-ai-agent.md).

## See also

- [Getting Started](../tutorial/getting-started.md) — run your first review
- [Architecture](architecture.md) — the fetch → compress → prompt → parse → post pipeline
- [Data and Privacy](data-and-privacy.md) — what is sent where
