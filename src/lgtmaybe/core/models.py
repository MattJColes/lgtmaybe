"""Frozen data contracts.

These pydantic models are the wire format between every track. They are frozen
in the foundation step: change them only by consensus, never to suit one track.
`extra="forbid"` makes typos and drift fail loudly instead of silently.
"""

from __future__ import annotations

from contextlib import suppress
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Side of the diff a comment attaches to, matching the GitHub review API.
Side = Literal["LEFT", "RIGHT"]

# The PR-label families lgtmaybe owns (F4). Shared vocabulary between the engine
# (which computes the labels) and the GitHub adapter (which reconciles them,
# removing stale labels with these shapes) — one definition so a renamed family
# can't silently break the reconcile.
EFFORT_PREFIX = "review-effort/"
SECURITY_LABEL = "possible-security-issue"
SPLITTING_LABEL = "consider-splitting"


class Severity(StrEnum):
    """Finding severity, ordered low → high for `min_severity` filtering."""

    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

    @property
    def rank(self) -> int:
        # Enum iteration order IS declaration order, so the members above are the
        # single source of the ordering — no second list to keep in step.
        return list(Severity).index(self)

    # All four order comparisons rank by severity. Defined explicitly (not via
    # functools.total_ordering, which skips operators str already defines) so
    # none can fall back to str's alphabetical order, where "critical" < "high".
    def __lt__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.rank < other.rank
        return NotImplemented

    def __le__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.rank <= other.rank
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.rank > other.rank
        return NotImplemented

    def __ge__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.rank >= other.rank
        return NotImplemented


class ReviewCategory(StrEnum):
    """A single review lens. The engine asks for each one in its own LLM call.

    ``intent`` checks the diff against the PR's stated intent (title, description,
    commit messages); it only runs when the context carries some stated intent.
    ``ponytail`` is the "lazy senior dev" lens — the best code is the code you
    never wrote — flagging code that needn't exist at all (YAGNI, reach for the
    standard library, do it in fewer lines).
    ``spec`` checks the diff against a specification the repository commits
    (OpenSpec, GitHub Spec Kit, Kiro); like ``intent`` it only runs when there is
    something to check against — here, a detected spec that matches the PR.
    """

    security = "security"
    correctness = "correctness"
    deprecation = "deprecation"
    tests = "tests"
    documentation = "documentation"
    performance = "performance"
    complexity = "complexity"
    intent = "intent"
    ponytail = "ponytail"
    spec = "spec"


class ReviewPreset(StrEnum):
    """How many model calls a review spends: the everyday path or the deep audit.

    ``fast`` (the default) covers all nine built-in categories in FOUR calls,
    one per concern: security, correctness (with stated intent folded in), code
    health, and artefacts (tests + documentation). The same four run on every
    provider — worker count changes the schedule, not the call count.
    ``full`` runs each of the nine categories as its own call for release
    branches and deep audits. An explicit ``categories`` list always wins over
    the preset.
    """

    fast = "fast"
    full = "full"


class Provider(StrEnum):
    """The backend selected by the `--provider` flag."""

    openai = "openai"
    openrouter = "openrouter"
    anthropic = "anthropic"
    bedrock = "bedrock"
    vertex = "vertex"
    azure = "azure"
    ollama = "ollama"
    # Any server speaking the OpenAI /v1 wire format, reached via --api-base:
    # DeepSeek's API, llama.cpp, LM Studio, vLLM, and other proxies. Key optional.
    openai_compatible = "openai-compatible"
    # GLM / Zhipu AI via litellm's native zai/ route; API-key auth (ZAI_API_KEY),
    # optional --api-base override for the China / coding-plan endpoints.
    zai = "zai"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StaticAnalysisTool(StrEnum):
    """A deterministic linter/SAST tool whose findings feed the review.

    The value is the binary name looked up on PATH, so it may differ from the
    member name where the binary is hyphenated.
    """

    ruff = "ruff"
    bandit = "bandit"
    semgrep = "semgrep"
    mypy = "mypy"
    gitleaks = "gitleaks"
    zizmor = "zizmor"
    # Member name differs from the binary: enum members cannot be hyphenated,
    # and `_run_tool` looks up `tool.value` on PATH.
    ast_grep = "ast-grep"
    osv_scanner = "osv-scanner"


class ToolMode(StrEnum):
    """How a tool's findings reach the review.

    ``hint`` is the original fusion behaviour: findings become an untrusted
    HINTS block the model confirms, contextualises, or discards. ``finding``
    skips the model entirely and posts the tool's result as a review comment.

    Which one is right is a property of the tool's precision, not a user
    preference: a secret is present or it isn't, so asking a model to "confirm
    or discard" a deterministic regex match only adds cost and doubt. A lint or
    a SAST heuristic genuinely benefits from a model judging whether it matters
    here. Per-tool defaults live in ``engine.static_analysis``; this enum is the
    override users reach for via ``static_analysis.tool_mode``.
    """

    hint = "hint"
    finding = "finding"


class StaticAnalysisConfig(_Strict):
    """Static-analysis fusion: deterministic tool findings as LLM grounding.

    When enabled, the installed tools run over the already-fetched changed-file
    texts (sandboxed subprocess, scrubbed environment, no network, never a
    checkout) and their findings enter each lens prompt as untrusted HINTS —
    "confirm, contextualise, or discard" — raising recall on the deterministic
    bugs models miss without posting raw linter noise. A tool that isn't
    installed is skipped silently, so the feature degrades to nothing on a
    minimal install.
    """

    # Global switch. Off by default: existing installs and zero-dependency
    # setups see no behaviour change and no subprocess ever runs.
    enabled: bool = False
    # Tools to run when enabled (each skipped silently if not installed).
    # `default=` (not default_factory) on purpose, like ReviewConfig.categories:
    # pydantic copies it per instance and it reaches the generated docs schema.
    tools: list[StaticAnalysisTool] = Field(default=list(StaticAnalysisTool))
    # Floor on the MAPPED severity of tool findings (ruff → low; bandit
    # LOW/MEDIUM/HIGH → low/medium/high; semgrep INFO/WARNING/ERROR →
    # info/medium/high; mypy error → medium, note → info). Hints below it are
    # dropped before prompting.
    min_severity: Severity = Severity.info
    # Per-tool overrides of `min_severity` — e.g. keep every bandit hit but
    # take only medium+ from ruff. A tool without an entry uses the global
    # floor; a tool's own entry always wins, in either direction.
    tool_min_severity: dict[StaticAnalysisTool, Severity] = Field(default_factory=dict)
    # Per-tool overrides of how findings reach the review (hint vs finding). A
    # tool without an entry uses the built-in default for that tool — see
    # `engine.static_analysis._DEFAULT_MODE`, which routes deterministic-claim
    # tools straight to posting and interpretive ones through the model.
    tool_mode: dict[StaticAnalysisTool, ToolMode] = Field(default_factory=dict)
    # Local semgrep rules file/dir passed as --config. semgrep is SKIPPED when
    # unset: its registry configs (`--config auto`) fetch over the network,
    # which the sandbox forbids.
    semgrep_rules: str | None = None
    # Local ast-grep rule file/dir. ast-grep ships no rules of its own, so it is
    # SKIPPED when unset — this is the deterministic sibling of `extra_lenses`:
    # your own structural rules, matched on code shape rather than by a model.
    ast_grep_rules: str | None = None


class ReviewFinding(_Strict):
    """A single inline review comment the model wants to post."""

    path: str
    # 1-based diff line. A 0/negative value is degenerate model output that maps to
    # no real changed line (re-anchoring finds nothing) or mis-posts on GitHub, so
    # reject it at the boundary — like a non-integer line, this fails the lens
    # loudly rather than posting a comment on a nonsensical line.
    line: int = Field(ge=1)
    side: Side = "RIGHT"
    severity: Severity
    title: str
    body: str
    # Concrete causal evidence for defect findings: the trigger, changed
    # behaviour, and observable impact. The engine requires a non-blank value
    # for built-in defect categories after it stamps `category`; gap and custom
    # findings may leave it None.
    failure_scenario: str | None = None
    suggestion: str | None = None
    # The verbatim text of the changed line this finding is about (no +/- marker).
    # The model can't count diff lines reliably, so the engine re-anchors `line`
    # to the real changed line whose content matches this — see engine._snap_findings.
    anchor: str | None = None
    # Engine-derived placement confidence (the model's value is ignored). True when
    # `line` is trustworthy — the model gave no anchor (we trust its line) or the
    # anchor matched a changed line. False when an anchor was given but matched no
    # changed line, so `line` is a guess: the GitHub adapter then demotes the
    # finding to the review summary rather than posting it inline on a wrong line.
    anchored: bool = True
    # Engine-derived (the model's value is ignored, like `anchored`). True when the
    # reflection pass judged this a BROAD change — a redesign, infrastructure or
    # API/contract change, or one needing independent verification — rather than a
    # safe, self-contained edit. The GitHub adapter renders broad findings in a
    # collapsed "Broader observations" section instead of inline, so the must-fix
    # list stays tight without dropping the observation.
    broad: bool = False
    # Reflection-derived confidence that this finding is real (0 = certainly a
    # false positive, 10 = certain), set by the self-reflection auditor's verdict
    # — never self-reported by the reviewing model. None when reflection is off
    # or the auditor omitted a score. Findings scoring below
    # `ReviewConfig.min_confidence` are dropped; the score is surfaced in output.
    confidence: int | None = Field(default=None, ge=0, le=10)
    # Engine-derived: the id of the lens (built-in ReviewCategory value or
    # custom lens id) whose call produced this finding — the model's own value
    # is overwritten. Drives the security label and category-matched
    # finding_rules; surfaced in JSON output. None only for legacy inputs.
    category: str | None = None

    @field_validator("severity", mode="before")
    @classmethod
    def _normalise_severity(cls, value: object) -> object:
        """Accept model-supplied severity in any case ("High", "MEDIUM", " low ").

        Models routinely capitalise severities. The Severity enum is lower-case,
        so an un-coerced "High" fails validation — and because findings are parsed
        as a batch, one mis-cased item would drop its valid siblings. Lower-casing
        here keeps the whole batch.
        """
        if isinstance(value, str):
            return value.strip().lower()
        return value


_BUILTIN_LENS_IDS: frozenset[str] = frozenset(c.value for c in ReviewCategory)

# Category prefix stamped on a finding that came from a deterministic tool
# rather than a lens. Lives here, not in the engine, so the CustomLens id
# validator can reserve it without importing the engine.
_SCAN_CATEGORY_PREFIX = "scan:"


class CustomLens(_Strict):
    """A user-defined review lens — a "skill file" run alongside the built-ins.

    The engine fans it out as its own focused LLM call (same pipeline as a
    built-in ``ReviewCategory``) and merges its findings with the rest. A lens is
    declared in **trusted** config (``.lgtmaybe.yml`` or a file referenced by
    ``lens_paths``), never from PR-author content, so its text is safe to put in
    the system prompt. Supplying a worked example (``example_diff`` +
    ``example_finding``) is optional but sharply improves a small model's output.
    """

    id: str
    instructions: str
    title: str = ""
    example_diff: str | None = None
    example_finding: ReviewFinding | None = None

    @field_validator("id")
    @classmethod
    def _id_is_valid(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("custom lens id must be a non-empty string")
        if cleaned in _BUILTIN_LENS_IDS:
            raise ValueError(f"custom lens id {cleaned!r} collides with a built-in category")
        # The engine keys "did a deterministic tool produce this?" on the `scan:`
        # category prefix, and treats such findings differently: they skip the
        # reflection audit and are dropped when they land off the diff. A lens
        # must not be able to claim that status for a model's output.
        if cleaned.startswith(_SCAN_CATEGORY_PREFIX):
            raise ValueError(
                f"custom lens id {cleaned!r} is reserved: the "
                f"{_SCAN_CATEGORY_PREFIX!r} prefix marks deterministic tool findings"
            )
        return cleaned

    @model_validator(mode="after")
    def _example_is_paired(self) -> CustomLens:
        if (self.example_diff is None) != (self.example_finding is None):
            raise ValueError("example_diff and example_finding must be provided together")
        return self


class ReviewResult(_Strict):
    """Structured-output envelope: the model returns ``{"findings": [...]}``.

    Many providers' JSON-schema mode (litellm ``response_format``) requires a
    top-level object, not a bare array, so the findings list is wrapped. Used to
    constrain model output to valid JSON; the parser also accepts a bare array.
    """

    findings: list[ReviewFinding]
    # One-round deferral (mid-review retrieval): file path(s) — and/or symbol
    # names — this lens must READ before it can decide. When non-empty and
    # `mid_review_retrieval` is on, the engine fetches that text read-only,
    # redacts it, and re-runs THIS lens once with it appended to the lens's
    # (uncached) block; the findings of both calls are merged. Optional with a
    # back-compat default, so a model that omits it — every model, when the
    # feature is off and the prompt never asks — still validates.
    needs: list[str] = Field(default_factory=list)


class Verdict(_Strict):
    """One reflection verdict: keep or drop the finding at ``index``."""

    index: int
    keep: bool
    # The auditor's actionability call: True when fixing this needs a broad change
    # (redesign / infra / API-contract / independent verification) rather than a
    # safe self-contained edit. Optional with a back-compat default so a model that
    # omits it still validates; the engine copies it onto the kept finding's
    # `broad` flag for collapsed rendering.
    broad: bool = False
    # Deferral (Track D): file path(s) — and/or symbol names — the auditor needs to
    # SEE before it can decide this verdict. When non-empty the engine fetches that
    # text read-only, redacts it, and re-judges the finding with it in context
    # (bounded to engine.retrieve.MAX_HOPS hops), instead of dropping a finding
    # merely because the referenced code wasn't in the diff. Optional with a
    # back-compat default so a model that omits it still validates.
    needs: list[str] = Field(default_factory=list)
    # The auditor's 0-10 confidence that a KEPT finding is real (0 = certainly a
    # false positive, 10 = certain). Optional with a back-compat default so a
    # model that omits it still validates; an unscored kept finding survives any
    # `min_confidence` threshold (safe default — never drop for a missing score).
    confidence: int | None = Field(default=None, ge=0, le=10)


class ReflectionResult(_Strict):
    """Structured-output envelope for the reflection pass: ``{"verdicts": [...]}``.

    A fixed-shape object (not a dynamic-key map) so it can be enforced as a JSON
    schema via litellm ``response_format``, the same way reviews are.
    """

    verdicts: list[Verdict]


class FindingRuleMatch(_Strict):
    """The selector of a finding rule. Every specified field must match (AND).

    An empty match selects every finding. ``path`` is an fnmatch glob against
    the repo-relative path (a ``**/`` prefix also matches at the repo root,
    like the path filters); ``category`` is the originating lens id;
    ``title_contains`` is a case-insensitive substring; ``min_severity``
    selects findings at or above that severity.
    """

    path: str | None = None
    category: str | None = None
    title_contains: str | None = None
    min_severity: Severity | None = None


class FindingRuleAction(_Strict):
    """What a matched rule does: drop the finding, or remap its severity."""

    drop: bool = False
    set_severity: Severity | None = None

    @model_validator(mode="after")
    def _has_an_effect(self) -> FindingRuleAction:
        if not self.drop and self.set_severity is None:
            raise ValueError("a finding rule action must drop or set_severity")
        return self


class FindingRule(_Strict):
    """One declarative post-processing rule, applied in list order.

    The safe alternative to arbitrary user hooks: rules can only filter or
    re-grade findings — no user code ever executes.
    """

    match: FindingRuleMatch = Field(default=FindingRuleMatch())
    action: FindingRuleAction


class DirectoryRule(_Strict):
    """Extra review instructions and context scoped to part of the repo.

    A monorepo is not uniform: ``payments/**`` wants strictness that would be
    noise in ``tests/**``, and reviewing ``src/**`` well may need a design doc
    the diff never shows. Each rule names the paths it applies to (fnmatch globs
    against the repo-relative path, a ``**/`` prefix also matching at the repo
    root — the same matcher the path filters use; an EMPTY list applies the rule
    everywhere, mirroring ``FindingRuleMatch``), free-text ``instructions``, and
    ``context_files`` read from the checked-out workspace.

    Both are trusted configuration: on ``pull_request_target`` the workspace is
    the BASE branch, so neither the instructions nor the context text is ever
    PR-author content.
    """

    paths: list[str] = Field(default_factory=list)
    instructions: str = ""
    context_files: list[str] = Field(default_factory=list)


class FileWalkthrough(_Strict):
    """One entry of a PR description's per-file walkthrough."""

    path: str
    summary: str = ""


class DescribeResult(_Strict):
    """Structured-output envelope for the describe pass.

    A fixed-shape object so it can be enforced as a JSON schema via litellm
    ``response_format``. Every field beyond the title is optional with an
    empty default, so a model that answers partially still validates; a fully
    unparseable answer falls back to the raw text.
    """

    title: str
    change_type: str = ""
    summary: str = ""
    walkthrough: list[FileWalkthrough] = Field(default_factory=list)
    intent_check: str = ""


class DiagramNode(_Strict):
    """One component in the model's presentation-agnostic change graph."""

    id: str
    label: str
    technology: str = ""
    description: str = ""
    change: Literal["unchanged", "changed", "new"] = "unchanged"


class DiagramEdge(_Strict):
    """One directed relationship between two diagram node ids."""

    source: str
    target: str
    label: str = ""


class DiagramStep(_Strict):
    """One ordered run-time interaction between two diagram node ids.

    Where an edge says two components are related, a step says what happens and
    when — the behaviour view of the same change. ``reply`` renders the dashed
    return arrow.
    """

    source: str
    target: str
    label: str = ""
    reply: bool = False


class DiagramResult(_Strict):
    """Structured-output envelope for the change-diagram pass.

    The model returns presentation-agnostic nodes, edges, and ordered steps.
    lgtmaybe renders Mermaid (a flowchart of the structure, a sequence diagram
    of the flow) and text locally so model-authored syntax never reaches a
    Mermaid fence.
    """

    title: str = ""
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)
    steps: list[DiagramStep] = Field(default_factory=list)
    notes: str = ""


class AnswerResult(_Strict):
    """Task-specific structured envelope for a slash-command answer."""

    answer: str


class TriageFileVerdict(_Strict):
    """One triage verdict: whether *path* needs the strong model, and how risky."""

    path: str
    review: bool = True
    risk: int = Field(default=5, ge=0, le=10)


class TriageResult(_Strict):
    """Structured-output envelope for the triage pass: ``{"files": [...]}``.

    A fixed-shape object so it can be enforced as a JSON schema via litellm
    ``response_format``, the same way reviews and reflection verdicts are.
    """

    files: list[TriageFileVerdict]


class ProviderResult(_Strict):
    """The normalised return of one LLM completion, with token usage."""

    text: str
    input_tokens: int
    output_tokens: int
    # Prompt-cache accounting, when the provider reports it: tokens read from a
    # previously cached prefix, and tokens written to create one. Zero on
    # providers/models without prompt caching (back-compat default).
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    # Tokens the model spent thinking before it wrote a word of the answer, when
    # the route reports them. A SUBSET of `output_tokens`, never an addition to
    # it — the two are added nowhere, or the budget double-counts. Zero on routes
    # that report no breakdown, which means "not reported", NOT "did no thinking".
    #
    # It is on the success path for a reason: read only off truncated calls (where
    # it was first surfaced, to name the cause) the number cannot answer the
    # question it exists for, because such a call has reasoning + findings >=
    # max_tokens by definition and so offers no healthy call to compare against.
    reasoning_tokens: int = 0
    # Completion attempts the adapter made to produce this result (1 = first try).
    # Feeds the timing instrumentation so a call that burned its retry budget is
    # distinguishable from one that was merely slow.
    attempts: int = Field(default=1, ge=1)


# A FAILED call has no ProviderResult to carry `attempts` home on, so the adapter
# stamps the count onto the exception instead and the instrumentation reads it
# back. Without this a call that burned its whole retry budget was recorded as
# `attempts=0` — indistinguishable from one that never retried at all, which is
# exactly the wrong impression when the failure is a timeout.
_ATTEMPTS_ATTR = "lgtmaybe_attempts"


def stamp_attempts(exc: BaseException, attempts: int) -> None:
    """Record on *exc* how many completion attempts the failed call cost.

    Best effort: an exception type that refuses attributes (``__slots__``) is not
    worth failing a review over — it just reports the attempts as unknown.
    """
    with suppress(Exception):
        setattr(exc, _ATTEMPTS_ATTR, attempts)


def attempts_of(exc: BaseException) -> int:
    """Attempts burned by the call that raised *exc*; 0 when unknown.

    0 means the failure never reached the adapter's retry loop (or the exception
    could not be stamped) — not "was not retried".
    """
    value = getattr(exc, _ATTEMPTS_ATTR, 0)
    return value if isinstance(value, int) and value > 0 else 0


class PRContext(_Strict):
    """Everything the engine needs about a PR — fetched via API, never checkout."""

    diff: str
    changed_files: list[str]
    base_sha: str
    head_sha: str
    repo: str
    pr_number: int
    # Head-revision text of reviewable changed files, keyed by path. Populated by
    # the gateway so the engine can pad hunks with surrounding lines; empty when
    # unavailable (the engine then reviews the bare diff).
    file_contents: dict[str, str] = Field(default_factory=dict)
    # The PR's stated intent: title + description on GitHub, commit names (the
    # first line of each commit message) everywhere. Attacker-controlled text —
    # the engine redacts it and wraps it as untrusted data before it reaches the
    # model, and only the intent lens carries it. Empty intent skips that lens.
    title: str = ""
    description: str = ""
    commit_messages: list[str] = Field(default_factory=list)
    # The PR's head branch name (``git rev-parse --abbrev-ref HEAD`` locally).
    # Read by the spec lens only, to match a PR against the committed spec it is
    # delivering: Spec Kit names the branch after the spec directory, and an
    # OpenSpec change-id usually matches it too. Attacker-controlled on a fork,
    # so it is only ever compared against directory names already on disk — it
    # never reaches a prompt. Empty when unavailable.
    head_branch: str = ""
    # Dependency manifests and lockfiles fetched for DETERMINISTIC SCANNING ONLY.
    # Separate from `file_contents` on purpose: that dict feeds hunk expansion,
    # suppression pragmas and reflection grounding, all of which end in a prompt,
    # and a resolved dependency tree belongs in none of them. Only the static
    # analysis runner reads this. Empty unless static analysis is enabled.
    scan_contents: dict[str, str] = Field(default_factory=dict)
    # Fingerprints of our own findings an authorised reviewer reacted 👎 to on a
    # previous run (read from GitHub each run — no local persistence). Suppression
    # drops matching findings, except high/critical security findings, which a
    # downvote can never hide. Populated by the CLI, empty by default.
    feedback_downvotes: frozenset[str] = frozenset()
    # How many of our OWN finding conversations from earlier runs are still
    # unresolved on this PR. Not findings of this run — they are the business
    # this run's count cannot see, because an incremental run may not re-review
    # their files at all. Reported so a "0 findings" result is never dressed up
    # as a clean PR while earlier findings sit unaddressed. Populated by the
    # GitHub gateway, 0 for the local CLI (no conversations to track).
    open_finding_threads: int = Field(default=0, ge=0)


class ReviewConfig(_Strict):
    """How to run one review: provider/model, severity floor, filters, caps."""

    provider: Provider
    model: str
    api_base: str | None = None
    # Severity floor: findings below this are dropped before posting. Defaults to
    # `low` (not `info`) so pure-info narration — a finding that merely restates
    # the diff ("X was removed") — never reaches the PR. Raise it to `medium` for
    # a weak/fast model that still over-reports.
    min_severity: Severity = Severity.low
    # Stricter floor applied only to UNANCHORED findings — ones whose verbatim
    # anchor matched no changed line, so the engine could not place them. A failed
    # anchor is a low-confidence signal (a weak model miscounting), so only a
    # high/critical guess is worth surfacing (demoted to the review body, never
    # posted on a line we can't stand behind); anything weaker is dropped.
    unanchored_min_severity: Severity = Severity.high
    # Merge-gate threshold. When set, after posting the review the GitHub adapter
    # creates a Check Run whose conclusion is `failure` if any surviving finding
    # is at or above this severity, else `success` — so a team can make lgtmaybe a
    # required check in branch protection. Enforcement rides the Check Run, never
    # PR approval state (lgtmaybe never sets approval state). None (default) = off,
    # non-breaking: no check run is created.
    fail_on: Severity | None = None
    include_paths: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    max_files: int = 50
    # Per-file diff-size cap: a single file whose patch is longer than this many
    # lines is dropped before any model call, and named in the summary notice.
    #
    # The name-based skip filter (`is_reviewable`) can only catch generated files
    # that ADMIT it in their name. It cannot catch a hand-named data blob — a
    # 154,000-line `clause_index.json`, an 18,000-line snapshot corpus — and those
    # are exactly the files that dominate a review's token bill while yielding
    # nothing worth commenting on. Size is the deterministic signal left, and
    # spending it here costs nothing: the file never reaches the provider, and the
    # recursive walk never splits it into hundreds of per-hunk calls.
    #
    # 2000 sits well above a real hand-written file change (a 2000-line single-file
    # patch is already past what any human reviews in one sitting) and far below
    # the generated artefacts above, so the default catches blobs without ever
    # silently dropping honest work. 0 disables the cap.
    max_file_diff_lines: int = Field(default=2000, ge=0)
    max_input_tokens: int = 100_000
    # RLM-style recursive walk: when a single file's diff exceeds
    # max_input_tokens, split it into per-hunk review calls instead of sending it
    # whole (where the model's context drops the tail). Nothing is dropped and
    # each call's context stays small — better recall on big files, especially for
    # smaller models. Files within budget are reviewed whole (context preserved).
    recursive: bool = True
    # Mid-review retrieval: let a review LENS defer once for bounded, read-only
    # codebase context. The shared rules tell every lens to hedge or omit a claim
    # that hinges on code outside the diff; with this on it may instead answer
    # `needs` (paths/symbols), and the engine fetches them (redacted, capped at
    # engine.retrieve.MAX_FETCH_FILES files and a quarter of max_input_tokens)
    # and re-runs that one lens with them. Bounded to ONE extra call per (batch,
    # lens) — which is exactly why it defaults OFF: a weak model defers
    # constantly and the recall win is unmeasured. Needs an injected file reader
    # (the GitHub gateway, or the local worktree), and reuses the same read-only
    # fetch path reflection's deferral uses. See docs/how-to/reduce-review-cost.md.
    mid_review_retrieval: bool = False
    # Cross-file symbol resolution during reflection: when the auditor defers a
    # finding because it needs to see a SYMBOL (function/class/type) defined outside
    # the diff, ast-grep locates that symbol's file in the corpus (the local
    # worktree, or a checkout of the trusted base branch) so the auditor re-judges
    # with the real definition instead of guessing. Needs the ast-grep binary (a
    # bundled dependency) and a corpus; degrades to the path-only fetch otherwise.
    symbol_resolution: bool = True
    # Ollama's context window (num_ctx). Ollama only — hosted providers manage
    # their own context window server-side and litellm won't forward this, so it
    # is ignored for them. None keeps the factory default (32768); raise it so a
    # large multi-file diff plus the emitted findings isn't truncated.
    num_ctx: int | None = None
    # Ceiling on surrounding context lines added around each hunk. The engine
    # uses min(context_lines, what the token budget allows); 0 disables it.
    context_lines: int = 20
    # Extend each hunk's LEADING pad up to the enclosing function/class
    # signature when ast-grep can cheaply find it (bounded reach; PR-Agent's
    # enclosing-scope expansion) — the signature explains a change better than
    # an arbitrary cut. Falls back to the fixed-line pad for unsupported
    # languages or any ast-grep failure. Off = fixed-line padding only.
    function_context: bool = True
    # Per-request timeout (seconds) for each provider completion call. None means
    # "auto": the factory picks a provider-aware default (long for providers that
    # may front a slow model — ollama, openai-compatible, openrouter — short for
    # direct cloud providers). An explicit value always wins.
    timeout: int | None = None
    # Sampling temperature for completions. Defaults to 0.0 for deterministic,
    # reproducible reviews (and steadier instruction-following on small models).
    temperature: float = 0.0
    # Ceiling on the tokens each model call may GENERATE (the findings JSON) — the
    # output counterpart to max_input_tokens. None (default) sends no cap, so the
    # model's own ceiling applies and a long findings payload is never truncated.
    #
    # Set it on a PREPAID route (OpenRouter and friends), which reserves
    # prompt + max_tokens against the balance BEFORE generating and falls back to
    # the model's full output ceiling when the request omits the cap — so an
    # uncapped review can be refused ("requires more credits ... you requested up
    # to 65536 tokens, but can only afford N") for credit it was never going to
    # spend. A cap sized to a real findings payload shrinks that reservation to
    # what the review actually costs.
    #
    # Sized too low it truncates the JSON mid-object and the call parses as a
    # failed lens, which is why it is opt-in rather than defaulted: reasoning
    # models spend this budget on thinking tokens too, so a value that suits a
    # plain model can starve a reasoning one.
    max_tokens: int | None = Field(default=None, ge=1)
    # How much of the output budget the model may spend THINKING before it
    # writes an answer — the knob `max_tokens` cannot express, because it caps
    # reasoning and findings TOGETHER and the model spends the reasoning first.
    #
    # Measured on this repo's own dogfood review: 5 of 9 lens calls burned
    # 32k-35k reasoning tokens — at or above the whole 32,768 `max_tokens`
    # ceiling — before writing a single finding, and the one large success wrote
    # ~733 tokens of findings after 28,909 tokens of thought. Raising the cap
    # from 16k to 32k did not fix it; reasoning simply expanded to fill the new
    # ceiling. That is the case for a separate lever rather than a bigger cap.
    #
    # None (default) sends nothing, so a route that does not accept the param is
    # unaffected. Values are litellm's normalised set — validated here so a typo
    # fails at config load rather than as a 400 on every lens call mid-review.
    reasoning_effort: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh", "default"] | None
    ) = None
    # Run the self-reflection pass that filters low-confidence findings. Disable
    # it (--no-reflect) when a weaker model drops valid findings during reflection.
    reflect: bool = True
    # Answer a PR author who replies inside a review conversation lgtmaybe opened
    # on a finding: the reply is answered in that same thread, using the finding
    # and its surrounding diff hunk as context. GitHub posting only (a
    # pull_request_review_comment event); the reply text is treated as untrusted
    # input. On by default; set false to leave finding threads unanswered.
    answer_replies: bool = True
    # Model used for the self-reflection (false-positive audit) pass. None falls
    # back to `model`. Point it at a stronger model so a weaker reviewer's findings
    # get audited by a better judge. Same provider/credentials as `model` — only
    # the model id changes (the provider client is built once).
    reflect_model: str | None = None
    # Human language for the reviewer's prose. When set, finding `title`/`body`
    # (and the describe/diagram prose) are written in this language, while the
    # structural fields (`path`, `line`, `side`, `severity`, `anchor`) and the
    # `suggestion` code stay untranslated. None (default) = English, and emits
    # the pre-language prompts byte-for-byte (the prompt-cache contract depends
    # on that default staying stable).
    language: str | None = None
    # Declarative finding post-processing, applied in order just before
    # posting: each rule's match (path glob / category / title substring /
    # severity floor, ANDed) selects findings and its action drops them or
    # remaps their severity. A safe alternative to arbitrary user hooks — no
    # user code ever runs. Empty (default) = no post-processing.
    finding_rules: list[FindingRule] = Field(default_factory=list)
    # Directory-scoped review instructions and context files: each rule's path
    # globs select the files it applies to (empty = everywhere), its
    # `instructions` are handed to every lens reviewing a batch that touches
    # them, and its `context_files` are read from the checked-out workspace and
    # included alongside. Both are trusted configuration (on
    # pull_request_target the workspace is the base branch, never the PR head).
    # Empty (default) = one uniform review across the whole repo. YAML-only,
    # like finding_rules and extra_lenses.
    directory_rules: list[DirectoryRule] = Field(default_factory=list)
    # Spec lens: when the repository drives its work from a committed
    # specification (OpenSpec, GitHub Spec Kit, Kiro), check the diff against the
    # spec it is delivering — requirements it falls short of, task-list entries
    # it ticks off without doing, and behaviour the spec never covers. On by
    # default, but gated on DETECTION: no spec system in the workspace, or no
    # spec matching this PR, and the lens is dropped before the fan-out, so a
    # repository without specs pays no call and no prompt bytes. When it does
    # run it is a lens of its own (a fifth call under the fast preset).
    spec_review: bool = True
    # Extra directory globs to search for specs, for a house layout the three
    # known systems do not describe (e.g. `docs/rfcs/*`). Each match is treated
    # as a spec directory. YAML-only, like directory_rules.
    spec_paths: list[str] = Field(default_factory=list)
    # Custom template for the review summary line. Placeholders: {count}
    # (findings posted), {provider}, {model}, {version} (the running lgtmaybe
    # release). None (default) keeps the built-in line, which names all of them;
    # a template that fails to format falls back to it too.
    summary_template: str | None = None
    # PR labels (GitHub posting only): attach a review-effort/1-5 size
    # estimate, a possible-security-issue flag when a high/critical
    # security-lens finding posts, and a consider-splitting hint when the diff
    # sprawls across many unrelated top-level directories. Derived entirely
    # from data the review already computes — no extra model calls.
    # Best-effort (a label failure never fails the review). Default off.
    pr_labels: bool = False
    # Auto-describe: when the GitHub Action is triggered by a PR being opened
    # (or reopened), post a structured description comment — title, change
    # type, summary, per-file walkthrough, intent check — before the review
    # runs. Idempotently updated in place on later /describe runs. A separate
    # concern from the review (either can be enabled independently), and a
    # describe failure never blocks the review. Default off.
    auto_describe: bool = False
    # Auto-diagram: like auto_describe, but posts a compact change diagram —
    # a Mermaid flowchart of the components the PR touches (with an ASCII
    # fallback), rendered natively in the comment — when a PR is opened or
    # reopened. Its own comment, updated in place on later /diagram runs.
    # Best-effort; a diagram failure never blocks the review. Default on —
    # no yaml needed; set false to opt out.
    auto_diagram: bool = True
    # Two-stage triage routing: when set, this cheap model runs FIRST over the
    # compressed per-file diffs, skipping files that plainly need no review
    # (pure formatting, trivial renames, generated content that slipped the
    # filter) and ranking the rest by risk — the strong `model` then reviews
    # only the survivors, riskiest first. A deterministic floor always
    # escalates security-relevant files (auth/crypto/IaC/CI paths, security
    # tokens in the patch, static-analysis hits, large hunks) — triage can
    # never skip them. Same provider/credentials as `model` (an all-ollama
    # setup pays nothing). None (default) disables triage entirely.
    # Trade-off: cheaper reviews at the risk of the triage model under-rating
    # a subtle file; the floor + review-when-unsure prompt bound that risk.
    triage_model: str | None = None
    # Drop findings the reflection auditor scores below this confidence (0-10).
    # 0 (the default) disables numeric filtering — reflection then prunes only
    # via its keep/drop verdicts, exactly as before the score existed. Findings
    # the auditor keeps but doesn't score always survive the threshold.
    min_confidence: int = Field(default=0, ge=0, le=10)
    # Finding fingerprints to permanently suppress — a team dismissing a known-fine
    # pattern. Each entry is a finding_fingerprint(path, title) hex id (surfaced in
    # the inline comment's hidden marker). Findings matching one are dropped before
    # reflection and posting. Set it in .lgtmaybe.yml; an inline `# lgtmaybe: ignore`
    # comment on (or just above) a flagged line suppresses that finding too.
    ignore_fingerprints: list[str] = Field(default_factory=list)
    # Feedback learning (GitHub posting only): on a re-run, suppress a finding a
    # human reacted 👎 (thumbs-down) to on its inline comment last time. The 👎
    # reactions live on GitHub and are re-read each run (no new persistence); a
    # downvoted finding's fingerprint is merged into ignore_fingerprints and
    # dropped before reflection and posting. Resolving a thread is NOT a suppress
    # signal — that means "fixed" and is handled by resolve-on-fix. Default on.
    learn_feedback: bool = True
    # Static-analysis fusion: run installed deterministic linters (ruff,
    # bandit, semgrep-with-local-rules) over the changed files and feed their
    # findings into the lens prompts as untrusted hints to confirm or discard.
    # Default off — see StaticAnalysisConfig.
    static_analysis: StaticAnalysisConfig = Field(default=StaticAnalysisConfig())
    # Commit-scoped incremental review: on a re-run, review only the diff of
    # the commits pushed since the last completed review (read back from a
    # hidden watermark in the summary comment) instead of the whole PR.
    # None means auto: on when the GitHub Action is triggered by a synchronize
    # push, off everywhere else (a from-scratch review is always full). Falls
    # back to a full review when there is no watermark, the branch was
    # force-pushed/rebased, or the compare fails. Findings on files outside
    # the increment stay open — they are only resolved when a run that
    # re-reviewed their file no longer produces them. `/review full` forces a
    # full re-review on demand.
    incremental: bool | None = None
    # Auto-resolve a previously-posted review conversation once its finding is
    # fixed: on a re-run, when a finding lgtmaybe flagged is no longer produced
    # and GitHub marks the thread outdated (the code under it changed), lgtmaybe
    # replies and resolves the conversation. GitHub posting only — ignored by the
    # local CLI review, which has no conversations to resolve.
    resolve_fixed: bool = True
    # Call-count preset (see ReviewPreset): `fast` (default) covers all nine
    # built-in categories in four calls, one per concern — security,
    # correctness, code health, artefacts — the same four on every provider;
    # `full` runs one call per lens. An explicit `categories` list overrides it.
    preset: ReviewPreset = ReviewPreset.fast
    # Review lenses to run. Each is asked in its own concurrent LLM call and the
    # findings are merged + deduped. Defaults to all of them (grouped per the
    # preset above); narrowing it to an explicit list disables the preset
    # grouping and runs exactly those lenses, one call each. `default=` (not
    # default_factory) on purpose: pydantic copies it per instance, and only a
    # plain default reaches the JSON schema that docs/generate_reference.py
    # renders.
    categories: list[ReviewCategory] = Field(default=list(ReviewCategory))
    # User-defined ("BYO") lenses run alongside the built-in categories — each
    # fans out as its own focused call and merges into the same findings. Loaded
    # from .lgtmaybe.yml (inline) or skill files via the loader's `lens_paths`.
    extra_lenses: list[CustomLens] = Field(default_factory=list)
    # Soft wall-clock ceiling for one review run, in seconds. Once it passes,
    # no further model calls are dispatched: in-flight calls finish, their
    # findings post, and the summary carries the existing "results may be
    # incomplete" notice naming the skipped calls. It can never turn a total
    # failure into a silent LGTM — a run where every call failed or was
    # skipped still fails loud. Generous by default (60 minutes — 2× the
    # generous per-call timeout, so one slow gateway/local call can't eat the
    # whole review budget); 0 disables the ceiling entirely.
    max_review_seconds: int = Field(default=3600, ge=0)
    # Soft billable-token ceiling for one review run (input + output summed
    # across every model call: lens fan-out, triage, reflection). Behaves
    # exactly like max_review_seconds — once passed, no further model calls are
    # dispatched, in-flight calls finish, their findings post, and the summary
    # carries a notice naming this knob. It can never turn a total failure into
    # a silent LGTM.
    #
    # 0 (the default) disables it. Unlike the wall-clock ceiling there is no
    # safe generous default: token spend scales with diff size, lens count and
    # batch count, so any figure that protects a small repo silently truncates
    # a large one's review. Measurement is always on (`--profile`, and the
    # structured `provider call` log lines) — read a real run's total, then set
    # this above it. See docs/how-to/reduce-review-cost.md.
    max_review_tokens: int = Field(default=0, ge=0)
    # Ceiling on concurrent review calls across the WHOLE fan-out (every
    # (batch, lens) task shares one pool). None means auto: 8 for hosted cloud
    # providers (their retry layer absorbs a capacity 429, and on bedrock cache
    # reads don't count against rate limits), 1 for ollama (a single instance
    # serves a model serially — concurrent calls just queue and time out), and
    # 1 for openai-compatible (a llama.cpp/LM Studio single-slot server wants
    # 1; a vLLM server batches happily at 8 — raise it explicitly for those).
    max_concurrency: int | None = Field(default=None, ge=1)
    # Constrain model output to the findings JSON schema via litellm
    # response_format (provider-native JSON mode). Keeps models from returning
    # prose/reasoning instead of findings. Disable for a model/provider that
    # doesn't support it (the lenient parser is the fallback).
    structured_output: bool = True
    # Reuse the shared prefix (system preamble + wrapped diff) across the
    # per-lens fan-out and the reflection call instead of re-paying full input
    # price for it on every call. On routes taking an explicit breakpoint
    # (anthropic, bedrock Claude/Nova, vertex Claude+Gemini, zai GLM,
    # openrouter's claude/gemini/glm/minimax) the adapter marks it with
    # cache_control;
    # backends that cache a repeated prefix automatically (openai, azure,
    # deepseek) need only the identical shape, which this also gives them. Also
    # enables the per-batch warm-up primer. Feature-detected per model and a
    # safe no-op on ollama/openai-compatible, so leaving it on costs nothing.
    prompt_cache: bool = True

    @model_validator(mode="after")
    def _lens_ids_are_unique(self) -> ReviewConfig:
        ids = [lens.id for lens in self.extra_lenses]
        if len(ids) != len(set(ids)):
            raise ValueError("extra_lenses ids must be unique")
        return self
