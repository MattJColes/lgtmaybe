"""Frozen data contracts.

These pydantic models are the wire format between every track. They are frozen
in the foundation step: change them only by consensus, never to suit one track.
`extra="forbid"` makes typos and drift fail loudly instead of silently.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    """A single review lens. The engine asks for each one in its own LLM call."""

    security = "security"
    correctness = "correctness"
    deprecation = "deprecation"
    tests = "tests"
    documentation = "documentation"
    performance = "performance"
    complexity = "complexity"


class Provider(StrEnum):
    """The backend selected by the `--provider` flag."""

    openai = "openai"
    openrouter = "openrouter"
    anthropic = "anthropic"
    bedrock = "bedrock"
    vertex = "vertex"
    azure = "azure"
    ollama = "ollama"


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


class ReviewConfig(_Strict):
    """How to run one review: provider/model, severity floor, filters, caps."""

    provider: Provider
    model: str
    api_base: str | None = None
    min_severity: Severity = Severity.info
    include_paths: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    max_files: int = 50
    max_input_tokens: int = 100_000
    # Ollama's context window (num_ctx). Ollama only — hosted providers manage
    # their own context window server-side and litellm won't forward this, so it
    # is ignored for them. None keeps the factory default (16384); raise it so a
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
    # Review lenses to run. Each is asked in its own concurrent LLM call and the
    # findings are merged + deduped. Defaults to all of them; narrow it to trade
    # thoroughness for fewer calls.
    categories: list[ReviewCategory] = Field(default=list(ReviewCategory))
    # Constrain model output to the findings JSON schema via litellm
    # response_format (provider-native JSON mode). Keeps models from returning
    # prose/reasoning instead of findings. Disable for a model/provider that
    # doesn't support it (the lenient parser is the fallback).
    structured_output: bool = True
