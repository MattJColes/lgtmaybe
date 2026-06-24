"""Frozen data contracts.

These pydantic models are the wire format between every track. They are frozen
in the foundation step: change them only by consensus, never to suit one track.
`extra="forbid"` makes typos and drift fail loudly instead of silently.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Side of the diff a comment attaches to, matching the GitHub review API.
Side = Literal["LEFT", "RIGHT"]


class Severity(StrEnum):
    """Finding severity, ordered low → high for `min_severity` filtering."""

    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_ORDER.index(self)

    def __ge__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.rank >= other.rank
        return NotImplemented


_SEVERITY_ORDER: list[Severity] = [
    Severity.info,
    Severity.low,
    Severity.medium,
    Severity.high,
    Severity.critical,
]


class ReviewCategory(StrEnum):
    """A single review lens. The engine asks for each one in its own LLM call.

    ``intent`` checks the diff against the PR's stated intent (title, description,
    commit messages); it only runs when the context carries some stated intent.
    ``ponytail`` is the "lazy senior dev" lens — the best code is the code you
    never wrote — flagging code that needn't exist at all (YAGNI, reach for the
    standard library, do it in fewer lines).
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


class ReviewFinding(_Strict):
    """A single inline review comment the model wants to post."""

    path: str
    line: int
    side: Side = "RIGHT"
    severity: Severity
    title: str
    body: str
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


class ReflectionResult(_Strict):
    """Structured-output envelope for the reflection pass: ``{"verdicts": [...]}``.

    A fixed-shape object (not a dynamic-key map) so it can be enforced as a JSON
    schema via litellm ``response_format``, the same way reviews are.
    """

    verdicts: list[Verdict]


class ProviderResult(_Strict):
    """The normalised return of one LLM completion, with token usage."""

    text: str
    input_tokens: int
    output_tokens: int


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
    include_paths: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    max_files: int = 50
    max_input_tokens: int = 100_000
    # RLM-style recursive walk: when a single file's diff exceeds
    # max_input_tokens, split it into per-hunk review calls instead of sending it
    # whole (where the model's context drops the tail). Nothing is dropped and
    # each call's context stays small — better recall on big files, especially for
    # smaller models. Files within budget are reviewed whole (context preserved).
    recursive: bool = True
    # Ollama's context window (num_ctx). Ollama only — hosted providers manage
    # their own context window server-side and litellm won't forward this, so it
    # is ignored for them. None keeps the factory default (32768); raise it so a
    # large multi-file diff plus the emitted findings isn't truncated.
    num_ctx: int | None = None
    # Ceiling on surrounding context lines added around each hunk. The engine
    # uses min(context_lines, what the token budget allows); 0 disables it.
    context_lines: int = 20
    # Per-request timeout (seconds) for each provider completion call. None means
    # "auto": the factory picks a provider-aware default (ollama gets a long one,
    # since local models are slow; cloud providers a short one). An explicit value
    # always wins.
    timeout: int | None = None
    # Sampling temperature for completions. Defaults to 0.0 for deterministic,
    # reproducible reviews (and steadier instruction-following on small models).
    temperature: float = 0.0
    # Run the self-reflection pass that filters low-confidence findings. Disable
    # it (--no-reflect) when a weaker model drops valid findings during reflection.
    reflect: bool = True
    # Model used for the self-reflection (false-positive audit) pass. None falls
    # back to `model`. Point it at a stronger model so a weaker reviewer's findings
    # get audited by a better judge. Same provider/credentials as `model` — only
    # the model id changes (the provider client is built once).
    reflect_model: str | None = None
    # Finding fingerprints to permanently suppress — a team dismissing a known-fine
    # pattern. Each entry is a finding_fingerprint(path, title) hex id (surfaced in
    # the inline comment's hidden marker). Findings matching one are dropped before
    # reflection and posting. Set it in .lgtmaybe.yml; an inline `# lgtmaybe: ignore`
    # comment on (or just above) a flagged line suppresses that finding too.
    ignore_fingerprints: list[str] = Field(default_factory=list)
    # Auto-resolve a previously-posted review conversation once its finding is
    # fixed: on a re-run, when a finding lgtmaybe flagged is no longer produced
    # and GitHub marks the thread outdated (the code under it changed), lgtmaybe
    # replies and resolves the conversation. GitHub posting only — ignored by the
    # local CLI review, which has no conversations to resolve.
    resolve_fixed: bool = True
    # Review lenses to run. Each is asked in its own concurrent LLM call and the
    # findings are merged + deduped. Defaults to all of them; narrow it to trade
    # thoroughness for fewer calls. `default=` (not default_factory) on purpose:
    # pydantic copies it per instance, and only a plain default reaches the JSON
    # schema that docs/generate_reference.py renders.
    categories: list[ReviewCategory] = Field(default=list(ReviewCategory))
    # User-defined ("BYO") lenses run alongside the built-in categories — each
    # fans out as its own focused call and merges into the same findings. Loaded
    # from .lgtmaybe.yml (inline) or skill files via the loader's `lens_paths`.
    extra_lenses: list[CustomLens] = Field(default_factory=list)
    # Constrain model output to the findings JSON schema via litellm
    # response_format (provider-native JSON mode). Keeps models from returning
    # prose/reasoning instead of findings. Disable for a model/provider that
    # doesn't support it (the lenient parser is the fallback).
    structured_output: bool = True

    @model_validator(mode="after")
    def _lens_ids_are_unique(self) -> ReviewConfig:
        ids = [lens.id for lens in self.extra_lenses]
        if len(ids) != len(set(ids)):
            raise ValueError("extra_lenses ids must be unique")
        return self
