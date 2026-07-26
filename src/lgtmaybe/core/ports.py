"""Boundary interfaces (ports).

Hexagonal architecture: these abstract base classes are the seams between the
core and the outside world. Adapters (litellm, github) implement them; the
engine depends only on these types. Frozen in the foundation step so the
parallel tracks can build against stable signatures.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import PRContext, ProviderResult, ReviewConfig, ReviewFinding

# A chat message in the provider-neutral shape litellm expects.
Message = dict[str, str]


class ProviderWallTimeout(TimeoutError):
    """Part of the provider contract: the call outlived its whole timeout budget.

    Defined here, not in the adapter, because the engine reacts to it — a payload
    the model could not finish in its budget is retried *smaller*, never repeated
    unchanged. Distinct from a backend's transport timeout (a connect/read blip),
    which stays an ordinary retryable error. A ``TimeoutError`` subclass, so
    callers matching on that keep working.
    """


class ProviderClient(ABC):
    """Port: an LLM backend that returns a normalised completion."""

    @abstractmethod
    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        """Run one completion and return text + token usage."""


class GitHubGateway(ABC):
    """Port: read a PR's context and post a review back."""

    @abstractmethod
    def get_pr_context(self) -> PRContext:
        """Fetch the PR diff and metadata via API (never check out PR code)."""

    @abstractmethod
    def post_review(
        self, findings: list[ReviewFinding], summary: str, diff: str | None = None
    ) -> None:
        """Post batched inline comments + one summary, idempotently.

        ``diff`` is the already-fetched PR diff used to map findings to inline
        positions; when omitted the adapter re-fetches it. Callers that already
        hold the context should pass it to avoid a redundant round-trip.
        """

    @abstractmethod
    def post_issue_comment(self, body: str) -> None:
        """Post a standalone comment to the PR conversation (in-thread reply)."""


class ReviewEngine(ABC):
    """Port: turn a PR context + config into findings and a summary."""

    @abstractmethod
    def review(self, ctx: PRContext, cfg: ReviewConfig) -> tuple[list[ReviewFinding], str]:
        """Produce (findings, summary) for the given PR and config."""
