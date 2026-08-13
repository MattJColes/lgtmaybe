# prompt-and-lenses Specification

## Purpose

How the review prompt is composed (`engine/prompt.py`): a lens-independent
cacheable preamble, per-lens checklists each with a worked example, the fast
preset's four-call grouping, and user-supplied custom lenses that fan out
identically to the built-ins.
## Requirements
### Requirement: Split-prefix prompt shape for caching

Every review call SHALL share a lens-independent system preamble and diff
prefix, with the lens checklist as the final uncached block, so routes with
cache breakpoints let lenses 2..N read the preamble-plus-diff from cache.
Providers without explicit cache support SHALL receive the user blocks joined
into one plain user message.
<!-- anchor: prompt.shared-preamble -->

#### Scenario: provider without cache support
- **WHEN** the model's route has no explicit cache breakpoint
- **THEN** the adapter joins the split user blocks into one plain user message

#### Scenario: output language configured
- **WHEN** `ReviewConfig.language` is set
- **THEN** the shared preamble carries a directive to write the `title`/`body`
  prose in that language while leaving structural fields and suggestion code
  unchanged

#### Scenario: output language unset
- **WHEN** `language` is unset
- **THEN** the preamble is byte-identical to the pre-language prompt

### Requirement: Every focused prompt teaches by worked example

Each lens prompt SHALL carry exactly one category-matched worked example with
a real hunk header, and the contract SHALL explain the `line`/`side`
arithmetic — the model is taught the coordinate system, not assumed to know it.
<!-- anchor: prompt.system -->

#### Scenario: security lens prompt is built
- **WHEN** the security lens call is composed
- **THEN** its prompt contains one security worked example with a real `@@`
  hunk header

### Requirement: Custom lenses are trusted config, fanned out uniformly

Users SHALL add lenses via `extra_lenses` (id + instructions, optional worked
example); the engine builds a uniform lens per built-in category and per
custom lens and fans them all through the same merge/dedupe/reflect pipeline.
Lens text enters the system prompt, so it is trusted config only — never
sourced from PR-author content.
<!-- anchor: prompt.custom-lens -->

#### Scenario: a custom lens joins a review
- **WHEN** `.lgtmaybe.yml` defines an extra lens
- **THEN** it runs as one more concurrent lens call, findings merged like any
  built-in

### Requirement: Fast preset is four distinct lenses on every provider

The default `fast` preset SHALL run every built-in category as FOUR lenses —
security, correctness, code health, and artefacts — one per concern. The lens
set SHALL NOT vary with the number of available workers: a single-worker
configuration runs the same four calls serially. Stated intent SHALL fold into
the correctness call rather than consume its own.
<!-- anchor: prompt.groups -->

#### Scenario: cloud default
- **WHEN** `fast` uses a cloud provider with auto-concurrency
- **THEN** its four calls are security, correctness, code health, and artefacts,
  and they may overlap

#### Scenario: local single-slot default
- **WHEN** `fast` uses Ollama with auto-concurrency
- **THEN** the same four calls run within the single-worker pool, serially

#### Scenario: intent with no dedicated call
- **WHEN** the fast preset runs and the PR states an intent
- **THEN** intent findings come out of the correctness call, attributed to the
  intent category

#### Scenario: everyday review covers artefacts
- **WHEN** the fast preset runs with no category override
- **THEN** tests and documentation are reviewed by the artefacts call

### Requirement: Review instructions and context scope to a directory

Extra instructions and reference files SHALL be scopeable to part of the repo
via `ReviewConfig.directory_rules`. A rule's path globs select the files it
applies to (an empty list applies it everywhere) using the same matcher as the
path filters; its instructions and the text of its `context_files` are handed to
every lens reviewing a batch that touches a matched file, and to no other batch.
Context text SHALL be read from the checked-out workspace — trusted base content
on `pull_request_target`, never the PR head — redacted and bounded by the
retrieval budget and file cap, with unreadable paths skipped. The rendered block
SHALL join the per-batch cacheable prefix, leaving the cross-batch system
preamble byte-identical to a review with no rules configured.
<!-- anchor: prompt.directory-rules -->

#### Scenario: a rule matches one batch
- **WHEN** a rule scoped to `payments/**` is configured and a review batches
  `payments/` and `docs/` files separately
- **THEN** only the `payments/` batch's calls carry the rule's instructions and
  context files

#### Scenario: context file names an unreadable path
- **WHEN** a `context_files` entry is missing, or resolves outside the workspace
  root
- **THEN** it is skipped and the review proceeds

#### Scenario: no rules are configured
- **WHEN** `directory_rules` is empty
- **THEN** no workspace file is read and the prompt is byte-identical to before
  the feature

### Requirement: The intent lens is told which files it cannot see

Each intent call SHALL name the PR's changed files absent from the diff it is
given, and the rubric SHALL rule that a claim about such a file is not shown,
not undone. The list SHALL be derived by subtracting the batch's paths from the
PR's changed files — covering the skip filter, path globs, file cap, triage,
incremental scope, and batching alike — capped, and carried inside the
neutralised intent block.
<!-- anchor: prompt.intent-visibility -->

#### Scenario: a generated file the skip filter dropped
- **WHEN** a PR states it regenerated a file that `is_reviewable` excludes
- **THEN** the intent call names it as not visible, so the kept promise is not
  reported as unfulfilled intent

#### Scenario: the PR spans several batches
- **WHEN** the diff is batched across multiple calls
- **THEN** each intent call names the files carried by the other batches

#### Scenario: the batch shows the whole PR
- **WHEN** no changed file is missing from the batch
- **THEN** no list is added and the block is byte-identical to before the feature

### Requirement: Defect prompts require a concrete failure scenario

Every built-in review prompt SHALL request a nullable `failure_scenario`.
Security, correctness, deprecation, and performance findings SHALL describe a
concrete trigger, the changed behaviour, and its observable impact regardless
of severity; tests, documentation, complexity, intent, and ponytail findings
SHALL return `null` rather than invent a causal story.
<!-- anchor: prompt.failure-scenario -->

#### Scenario: correctness lens finds a low-severity defect
- **WHEN** the correctness lens reports a defect as `low`
- **THEN** it still returns a concrete `failure_scenario`

#### Scenario: tests lens reports missing coverage
- **WHEN** the tests lens reports a real coverage gap
- **THEN** it returns `failure_scenario: null`

### Requirement: The model is not asked what a scanner answers better

The review prompt SHALL drop an ask whenever a deterministic scanner is
configured to report that class of finding itself. Dependency claims resting on
knowledge published after training — whether a version has a known advisory,
whether a package is abandoned — go when a vulnerability scanner reports them,
from both the focused and the merged lens. The committed-secret ask goes when a
secret scanner reports them: redaction has already rewritten matched secrets to
the reviewer's own marker, so the lens is asked for what it was prevented from
seeing. Claims no scanner answers (deprecated APIs, end-of-life runtimes,
typosquats, licence conflicts; secrets reaching a log) MUST remain. The decision
SHALL derive from configuration, not from which binaries happen to be installed,
so prompts stay reproducible.
<!-- anchor: prompt.dependency-health -->

#### Scenario: a vulnerability scanner posts findings
- **WHEN** a scanner is configured to report dependency advisories directly
- **THEN** neither lens asks the model for advisory or abandonment claims

#### Scenario: a secret scanner posts findings
- **WHEN** a scanner is configured to report committed secrets directly
- **THEN** the security lens drops the hardcoded-secret ask, keeping the rest

#### Scenario: no scanner covers the class
- **WHEN** no such scanner will run
- **THEN** the prompt is unchanged and the model reports them as before

### Requirement: The spec lens judges the diff against a committed specification

A review SHALL check the diff against a specification the repository commits
(OpenSpec, GitHub Spec Kit, Kiro, or a `spec_paths` layout) when one is detected
AND matches the PR, reporting both the diff falling short of the spec and the
spec failing to cover the diff. Detection is a filesystem probe and selection is
deterministic — the PR editing a spec, a branch or stated intent naming one —
so no match SHALL skip the lens with no prompt bytes and no model call. Spec
text SHALL be treated as untrusted data in its own neutralised block, carried
only on the spec call, and read from the workspace except for files the PR
changes, which come from its head text.
<!-- anchor: prompt.spec-lens -->

#### Scenario: no spec system in the repository
- **WHEN** the workspace holds no known spec layout
- **THEN** the lens never runs and the prompt is byte-identical to a build
  without the feature

#### Scenario: a spec directory unrelated to the PR
- **WHEN** several specs exist and none is edited, named by the branch, or named
  by the stated intent
- **THEN** no spec is selected and the lens is skipped

#### Scenario: the PR commits the spec it implements
- **WHEN** a selected spec file is among the PR's changed files
- **THEN** its head text is used, not the base branch's copy

#### Scenario: a task the PR ticked off
- **WHEN** the diff flips a `tasks.md` checkbox from unticked to ticked
- **THEN** that entry is carried into the spec block as a claim to verify
