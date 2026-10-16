# conversation-and-overview Specification

## Purpose

The comments lgtmaybe posts beyond the review: slash commands routed to
the same engine (`cli/slash.py`), conversational answers, and the
automatic change overview (description, High Impact Areas and diagrams),
including when it stands down and how the starter workflows and the docs
homepage present it.

## Requirements

### Requirement: Slash commands route to the same engine

`issue_comment` events SHALL parse into commands — `/review` (with `full`
forcing a full re-review), `/improve`, `/ask <q>` replying in-thread from a
task-specific answer object, `/describe` upserting a standalone structured
description, and `/diagram` upserting the change overview (description, high
impact areas, and Mermaid diagrams rendered locally from structured graph
data) — all dispatched to the same engine and provider stack. A command SHALL
run only for a commenter the gateway confirms has write access; anyone else's
(or an unverifiable one) is ignored, exit zero, with no model call.
<!-- anchor: cli.slash -->

#### Scenario: reviewer comments /review full
- **WHEN** the comment body is `/review full`
- **THEN** a full review runs, bypassing triage and incremental scoping

#### Scenario: a drive-by commenter types /review
- **WHEN** the commenter has no write access to the repository
- **THEN** the command is ignored with a log line and nothing is posted

#### Scenario: reviewer asks a question
- **WHEN** the comment body is `/ask <question>` and the provider returns a valid answer object
- **THEN** only the validated answer text is posted in-thread

#### Scenario: ask provider returns the wrong schema
- **WHEN** `/ask` returns object or array JSON without a valid answer
- **THEN** the raw JSON is not posted and the comment explains that no valid answer was produced

#### Scenario: reviewer comments /diagram
- **WHEN** the comment body is `/diagram`
- **THEN** the change overview upserts under the diagram marker family, its
  typed nodes and edges rendered into Mermaid and text views with stable ids,
  escaped labels, compact cards, and change markers on nodes

#### Scenario: provider returns diagram syntax instead of graph data
- **WHEN** the diagram provider ignores the graph contract and returns diagram
  source (C4, a Mermaid fence) with no nodes
- **THEN** that source is not posted as Mermaid and the comment explains that no
  valid diagram was produced

### Requirement: Conversational answers are directly actionable

Provider prompts for `/ask` answers SHALL require the response to begin with the
direct answer, omit preamble, tangents, recap, and closing pleasantries, use
numbered steps only when the work is genuinely multi-step, and end with one
concrete next action only when action remains. Purely informational answers
SHALL stop after answering instead of inventing a task for the reader.
<!-- anchor: cli.response-style -->

#### Scenario: User asks a direct question
- **WHEN** `/ask` requests information that needs no follow-up work
- **THEN** the answer leads with the result and stops without a fabricated next action

#### Scenario: Answer requires several actions
- **WHEN** an answer requires more than one bounded action
- **THEN** those actions are presented as the fewest numbered steps that still work

#### Scenario: the diff is context for a question, not a review request
- **WHEN** the diff is wrapped for `/ask`
- **THEN** it carries the same neutralised untrusted-data guard as a review diff
  but neither the review task restatement nor the review lead-in, which would ask
  for the findings JSON object from a call whose answer is prose

#### Scenario: the model answers with a machine envelope
- **WHEN** the response is entirely a JSON object or array
- **THEN** it is refused rather than posted, while prose that merely quotes braces
  or a fenced JSON example is relayed

### Requirement: Stale review-comment events are inert

The Action SHALL exit successfully on a `pull_request_review_comment` event
before loading review configuration, constructing a provider, or accessing
GitHub, so an obsolete workflow trigger cannot spend tokens or post output.
<!-- anchor: cli.review-comment-noop -->

#### Scenario: an old workflow delivers a review-comment event
- **WHEN** the Action receives `pull_request_review_comment`
- **THEN** it exits successfully without reading review config, calling a
  provider, or posting to GitHub

### Requirement: Change diagrams show structure and sequence

The change overview's diagram call SHALL render two complementary views: a
Mermaid flowchart of the components the change touches, and a Mermaid sequence
diagram of the ordered run-time interactions it alters. A summary SHALL head the
comment — the description when one is enabled, otherwise the diagram's own —
leading with the highest-impact change, keeping one change per sentence, and
omitting preamble, process recap, filler, and tangents. Steps referencing
unknown components SHALL be dropped, the step count SHALL be bounded,
participant and message text SHALL be escaped with Mermaid entity codes, and the
sequence view SHALL be omitted when the model reports no run-time flow.
<!-- anchor: cli.diagram-sequence -->

#### Scenario: change alters a run-time flow
- **WHEN** the provider returns a change summary and ordered steps between known
  components
- **THEN** the summary renders above a `sequenceDiagram` beside the flowchart
  under `Structure` and `Sequence` headings, each view with its own text version
  and link

#### Scenario: change has no run-time flow
- **WHEN** the provider returns a change summary and no steps
- **THEN** the comment carries the summary and flowchart, with no sequence
  section

#### Scenario: the same overview printed in a terminal
- **WHEN** `lgtmaybe diagram` prints the body locally
- **THEN** the summary remains above the diagrams, each collapsible text version
  becomes a labelled section with its HTML wrapper removed, and the Mermaid
  source stays intact to paste elsewhere

### Requirement: The change overview calls out high impact areas

The change overview SHALL carry a High Impact Areas section from its own
structured call, naming the changes whose blast radius reaches beyond the diff —
infrastructure, security posture, availability, data migrations, backups and
recovery, compatibility, observability, dependencies, cost, and compliance.
Deterministic path signals SHALL ground that call as untrusted data and SHALL
floor its result, so an area implicated by a changed path is named even when the
model reports nothing for it. A failed or unparseable call SHALL still render
those signals rather than dropping the section, an empty result SHALL name what
was checked, model-reported paths absent from the change SHALL be dropped, and
the section SHALL never fail the run.
<!-- anchor: cli.overview-high-impact -->

#### Scenario: model reports nothing for a changed sensitive path
- **WHEN** the change edits an infrastructure path and the model returns no area for it
- **THEN** the section still names that area and path, marked as not assessed

#### Scenario: high-impact call fails
- **WHEN** the provider raises or returns output that cannot be parsed
- **THEN** the section renders the path signals with a notice that the model
  assessment is unavailable, and the rest of the overview still posts

#### Scenario: nothing qualifies
- **WHEN** the model returns no areas and no path signals matched
- **THEN** the section states that none were detected and lists the areas checked

### Requirement: The automatic overview defers to an existing one

The Action SHALL NOT post its automatic change overview when the pull request already diagrams the change: a fenced Mermaid block (the diagram kind every forge renders) in the PR description or in a conversation comment it did not post itself. Its own earlier overview (this provider/model's diagram marker) SHALL keep the slot and keep refreshing, and another lgtmaybe setup's overview SHALL count as existing. A skipped overview SHALL be named in the review summary with the `/diagram` command that posts one, and the posted review result alone SHALL be the completion watermark for that run. A failed comment listing SHALL still check the description and otherwise post as usual. Slash commands SHALL never consult the check.
<!-- anchor: cli.existing-overview -->

#### Scenario: another tool already posted a walkthrough diagram
- **WHEN** an `opened` or `synchronize` run finds a Mermaid fence in another author's PR comment
- **THEN** the review posts without the overview and its summary names that comment and `/diagram`

#### Scenario: lgtmaybe posted the overview earlier
- **WHEN** the conversation carries this setup's own diagram marker beside another tool's diagram
- **THEN** the overview refreshes as usual

#### Scenario: a reviewer asks for the overview anyway
- **WHEN** the comment body is `/diagram` on a PR that already carries a diagram
- **THEN** the overview upserts as usual

### Requirement: Starter workflows enable the automatic change overview

The supplied GitHub Actions starter workflows SHALL rely on the automatic change overview being enabled by default, and the dogfood workflow SHALL keep the same setting while using the faster default review preset. When enabled, the overview SHALL refresh on `opened`, `reopened`, and `synchronize` events from the full current PR context (unless the PR already diagrams the change, below), post after the review result, and carry the head marker that proves the end-to-end run completed. When explicitly disabled, the posted review result alone SHALL be the completion watermark.
<!-- anchor: cli.starter-workflow-diagrams -->

#### Scenario: New repository adopts a supplied workflow
- **WHEN** a maintainer copies a supplied provider workflow into a repository
- **THEN** the workflow sets no `auto_diagram` input, since the default enables it

#### Scenario: Faster default is adopted
- **WHEN** the supplied workflow runs a default review
- **THEN** the automatic change overview remains enabled

#### Scenario: New push replaces an opened review
- **WHEN** a `synchronize` event replaces or follows the pull request's `opened`
  review while the automatic overview is enabled
- **THEN** the surviving run posts or updates the change overview

#### Scenario: Gitea reports a push as synchronized
- **WHEN** a Gitea Actions event payload carries `"action": "synchronized"`
- **THEN** the overview refreshes exactly as on GitHub's `synchronize`

#### Scenario: A new head completes
- **WHEN** a non-partial review and required diagram both post for the current head
- **THEN** later synchronize runs may use that head as their hybrid-review base

#### Scenario: Diagram generation fails
- **WHEN** the automatic overview is enabled and the current-head diagram cannot be generated or posted
- **THEN** the run fails without advancing completion, even if its review result already posted

### Requirement: Homepage demonstrates change diagrams

The main documentation homepage SHALL render a representative C4-style Mermaid
change diagram and link readers to the detailed change-diagram guide.
<!-- anchor: cli.docs-homepage-diagram -->

#### Scenario: Visitor opens the docs homepage
- **WHEN** a visitor opens the main documentation homepage
- **THEN** they see a rendered C4-style change-diagram example near the feature
  description and can follow its link to the full guide

### Requirement: Homepage diagram conveys change breadth

The main documentation homepage SHALL use a representative C4-style Mermaid
example that distinguishes changed and new elements and maps a change across
multiple cooperating containers.
<!-- anchor: cli.docs-homepage-diagram-breadth -->

#### Scenario: Visitor evaluates the diagram feature
- **WHEN** a visitor views the homepage change-diagram example
- **THEN** they can see changed and new elements connected across application,
  asynchronous, data, and external-service boundaries

### Requirement: Homepage overview stays concise

The main documentation homepage SHALL introduce every review category, its
trust boundaries, and its pull-request commands in no more than 400 source words
before the "Start here" section.
<!-- anchor: cli.docs-homepage-overview -->

#### Scenario: Visitor scans the homepage
- **WHEN** a visitor reads from the hero to the "Start here" section
- **THEN** they reach the commands and diagram without losing any review category
