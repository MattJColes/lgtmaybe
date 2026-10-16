"""Boundary interfaces (ports).

Hexagonal architecture: these abstract base classes are the seams between the
core and the outside world. Adapters (litellm, github) implement them; the
engine depends only on these types. Frozen in the foundation step so the
parallel tracks can build against stable signatures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .models import ActiveFinding, PRContext, ProviderResult, ReviewConfig, ReviewFinding

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


class ProviderTruncated(Exception):
    """Part of the provider contract: the answer ran out of output tokens.

    Distinct from a model that answered badly. The response is cut off
    mid-token, so it can never parse — but the cause is a ceiling, not a prompt,
    and the two send a maintainer to different fixes. Reported as its own
    failure so the notice on the PR names the ceiling and the knob that moves
    it, rather than "unparseable model output".

    Usually this says something about the *payload*: one call was asked to cover
    more than it could finish saying. The engine then reacts as it does to a wall
    timeout — split the batch and review the pieces — rather than failing the
    whole lens. But not always, which is what the counts below are for.

    ``text`` carries the cut-off body. It is not usable as an answer, but the
    findings the model completed before the cut are real work, and the engine
    salvages them from it exactly as the parser does for a truncation it detects
    itself. It rides on the error rather than being returned, so a caller cannot
    take the salvage without also seeing that the lens was cut short.

    ``reasoning_tokens`` (None when the route reports no breakdown — which is not
    the same as zero), ``output_tokens`` (the ceiling actually reached) and
    ``input_tokens`` carry the *diagnosis* as data rather than as prose.
    ``input_tokens`` is here for a second reason: a truncation is routinely the
    most expensive call in a run, and the spend ceiling has to be able to charge
    for it. Reporting a failure as free is how a runaway hides from the very
    budget that exists to stop it. A reasoning model spends this
    same budget on thought, so a truncation where the thinking accounts for
    essentially the whole ceiling is not a payload problem at all: covering less
    does not shrink a thinking budget, and only `reasoning_effort` moves it. The
    caller must be able to tell the two apart without re-reading our own message.

    ``cost_usd`` carries the same principle to the price column: the costliest
    call of the run usually fails, and a failure that reports no cost hides from
    the cost line the way a runaway once hid from the token ceiling. None when
    the adapter declines to price it.
    """

    def __init__(
        self,
        message: str,
        *,
        text: str = "",
        reasoning_tokens: int | None = None,
        output_tokens: int | None = None,
        input_tokens: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        super().__init__(message)
        self.text = text
        self.reasoning_tokens = reasoning_tokens
        self.output_tokens = output_tokens
        self.input_tokens = input_tokens
        self.cost_usd = cost_usd


class ProviderInputTooLarge(Exception):
    """Part of the provider contract: the prompt outgrew the model's context window.

    The third payload-sized failure, beside :class:`ProviderWallTimeout` (ran out
    of time) and :class:`ProviderTruncated` (ran out of room to answer): this
    one ran out of room to *ask*. The backend refuses the request outright — a
    400 naming the window — so there is no partial answer to salvage, and
    re-sending the identical prompt can only fail identically. But a smaller
    prompt succeeds, and only the engine holds the batch, so the engine reacts
    exactly as it does to the other two: split the batch and review the pieces.

    Named here rather than left as the adapter's generic bad request because the
    engine must tell it apart from a failure no later call can fix (a dead key,
    a spent quota): those end the lens, this one earns the split.
    """


class DiffUnavailable(Exception):
    """Part of the gateway contract: the host will not serve the change's diff.

    GitHub answers the ``.diff`` media type with **406 Not Acceptable** once a
    pull request passes 300 files or 20,000 lines — a limit of the host, not a
    transport fault, so no retry and no other token changes the answer. Fetching
    the diff via the API is the only way the reviewer ever reads PR content
    (never a checkout, which is what keeps ``pull_request_target`` safe), so with
    it refused there is nothing to review.

    Named so the CLI can tell it from every other fetch failure: those are
    reported as a failed review and exit non-zero, this one is disclosed as a
    review that did not happen — a notice carrying the host's reason, nothing
    stamped complete, exit zero. A silent LGTM would be the bug; so would a
    red job for a PR the reviewer was never able to look at.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ProviderClient(Protocol):
    """Port: an LLM backend that returns a normalised completion."""

    def complete(self, messages: list[Message], model: str, **opts: Any) -> ProviderResult:
        """Run one completion and return text + token usage."""
        ...


class ReviewGateway(Protocol):
    """Port: read a change request's context and post a review back.

    The required surface, and deliberately small: a forge adapter that
    implements these three methods produces a working review. Everything
    richer — incremental re-review, thread resolution, labels, checks — is an
    optional capability declared by one of the ``Supports*`` protocols below,
    and the CLI degrades gracefully when an adapter does not offer it. That is
    what lets a new forge ship a useful adapter before it ships a complete one.
    """

    def get_pr_context(self) -> PRContext:
        """Fetch the PR diff and metadata via API (never check out PR code)."""
        ...

    def post_review(
        self, findings: list[ReviewFinding], summary: str, diff: str | None = None
    ) -> None:
        """Post batched inline comments + one summary, idempotently.

        ``diff`` is the already-fetched PR diff used to map findings to inline
        positions; when omitted the adapter re-fetches it. Callers that already
        hold the context should pass it to avoid a redundant round-trip.
        """
        ...

    def post_issue_comment(self, body: str) -> None:
        """Post a standalone comment to the PR conversation (in-thread reply)."""
        ...


# ---------------------------------------------------------------------------
# Optional capabilities
# ---------------------------------------------------------------------------
#
# Beyond the required port above, the CLI probes a gateway for these groups and
# skips the corresponding feature when they are absent. They are grouped by
# feature rather than by method because each group is semantically
# all-or-nothing: half of an incremental review is worse than none of it.
#
# ``runtime_checkable`` protocols check method *presence*, not signatures, so
# they are a checklist for adapter authors rather than a proof of correctness.
# ``tests/test_ports.py`` asserts the shipped adapters satisfy the ones they
# claim, which is what keeps this list honest.


@runtime_checkable
class SupportsFileContents(Protocol):
    """Read file text at the change's head, for context expansion and retrieval."""

    def get_file_contents(self, path: str) -> str | None:
        """Fetch one file's head text via API — never a checkout (fork-safe)."""
        ...


@runtime_checkable
class SupportsBaseCheckout(Protocol):
    """Expose a lazily-cloned tree of the trusted base branch for symbol lookup."""

    def base_checkout_root(self) -> Path | None:
        """Root of a read-only clone of the base branch, or None if unavailable."""
        ...


@runtime_checkable
class SupportsDescribe(Protocol):
    """Upsert a structured PR description comment in its own marker family."""

    def post_describe_comment(self, body: str) -> None:
        """Post or edit-in-place the description comment."""
        ...


@runtime_checkable
class SupportsDiagram(Protocol):
    """Upsert a change-diagram comment in its own marker family."""

    def post_diagram_comment(self, body: str, *, completed_sha: str | None = None) -> None:
        """Post or edit-in-place the diagram comment."""
        ...


@runtime_checkable
class SupportsConversation(Protocol):
    """Read the comments already on the change's conversation."""

    def list_conversation_comments(self) -> list[tuple[str, str]]:
        """Every conversation comment as ``(author, body)``, oldest first."""
        ...


@runtime_checkable
class SupportsCompletionMark(Protocol):
    """Hear whether this run reviewed the head completely, before it posts.

    Split from ``SupportsIncremental`` (issue #832): Gitea and Azure DevOps
    take the stamp as the gate for resolve-on-fix — a partial run or a failure
    notice is missing findings, not reporting them fixed — without offering
    incremental review. On an incremental gateway it is also the watermark.
    """

    def mark_reviewed(self, head_sha: str | None) -> None:
        """Stamp ``head_sha`` as reviewed (success path only); None clears it."""
        ...


@runtime_checkable
class SupportsIncremental(SupportsCompletionMark, Protocol):
    """Review only the commits pushed since the last successful review.

    Requires somewhere durable to stamp a watermark (in practice an editable
    bot comment, written through the inherited ``mark_reviewed``) and an API
    for the diff between two commits.
    """

    def last_reviewed_sha(self) -> str | None:
        """The head SHA stamped by the last review, or None."""
        ...

    def last_completed_sha(self, *, diagram_required: bool) -> str | None:
        """The head SHA of the last run that finished every required artefact."""
        ...

    def compare_diff(self, base_sha: str, head_sha: str) -> str | None:
        """Unified diff between two commits, or None when not comparable."""
        ...

    def set_incremental_scope(self, paths: set[str] | None) -> None:
        """Limit resolve-on-fix to the files this increment re-reviewed."""
        ...


@runtime_checkable
class SupportsDiffFingerprint(SupportsIncremental, Protocol):
    """Stamp the whole change's diff fingerprint beside the watermark.

    A new head whose diff fingerprints the same as the reviewed one (a rebase
    that only moved a stacked pull request onto its merged parent) then has
    nothing new to review. A refinement of incremental review: the fingerprint
    is only compared against the head ``last_reviewed_sha`` names.
    """

    def last_reviewed_diff(self) -> str | None:
        """The fingerprint stamped with ``last_reviewed_sha``, or None."""
        ...

    def mark_reviewed_diff(self, fingerprint: str | None) -> None:
        """Stamp the whole diff's fingerprint (success path only); None clears it."""
        ...


@runtime_checkable
class SupportsScanManifests(Protocol):
    """Ask the gateway to also fetch dependency-manifest text for scanning.

    Split from ``SupportsIncremental`` (issue #774): Gitea and Azure DevOps
    serve manifest text without offering incremental review, so the two grow
    separately.
    """

    def set_scan_manifests(self, enabled: bool) -> None:
        """Fetch dependency-manifest head text on the next context fetch."""
        ...


@runtime_checkable
class SupportsThreadResolution(Protocol):
    """Reply in, and resolve, the conversations opened by earlier findings."""

    def list_active_findings(self) -> list[ActiveFinding]:
        """Open finding conversations, keyed by their hidden ids."""
        ...

    def set_validated_fixed_threads(self, thread_ids: set[str]) -> None:
        """Nominate the threads confirmed fixed and safe to resolve."""
        ...

    def reply_in_thread(self, thread_id: str, body: str) -> None:
        """Reply to one existing review conversation."""
        ...

    def count_open_finding_threads(self) -> int:
        """How many finding conversations are still open."""
        ...


@runtime_checkable
class SupportsLabels(Protocol):
    """Reconcile lgtmaybe's own label families on the change request."""

    def apply_pr_labels(self, labels: list[str]) -> None:
        """Add/remove only labels in lgtmaybe's families, best-effort."""
        ...


@runtime_checkable
class SupportsChecks(Protocol):
    """Report the review as a commit status / check run."""

    def create_check_run(self, head_sha: str, conclusion: str, title: str, summary: str) -> None:
        """Publish the review outcome against ``head_sha``."""
        ...


@runtime_checkable
class SupportsPermissions(Protocol):
    """Tell whether a user may push — the bar for running a slash command."""

    def has_repo_write(self, login: str) -> bool:
        """Whether *login* has write (or higher) access; False when unverifiable."""
        ...


@runtime_checkable
class SupportsFeedback(Protocol):
    """Read 👎 reactions so a rejected finding stops being resurfaced."""

    def list_downvoted_fingerprints(self) -> set[str]:
        """Fingerprints of findings downvoted by someone with write access."""
        ...


# ---------------------------------------------------------------------------
# Optional provider capabilities
# ---------------------------------------------------------------------------
#
# The mirror image of the gateway groups above, probed by the ENGINE on the
# ``ProviderClient`` adapter. One protocol, not one per method: these are the
# adapter-side halves of the engine's recovery steps (fitting the batching
# budget, the schema-less re-ask, the truncation ladder) and of disclosing which
# model answered, and the engine decides which step to take. Kept together so
# the engine's real dependency on the adapter is visible in one place. A step an
# adapter cannot take is answered with its neutral value (None, True, a no-op),
# never by leaving the method out.
#
# What a call LOST on the way to the model (its schema, its temperature) is not
# here: it rides each ``ProviderResult`` (or a failed call's exception, see
# ``models.stamp_degradation``), so the engine tallies it per review and an
# adapter shared by two reviews never reports one's drop in the other.
#
# The member names are the ones the engine calls, so renaming one on the adapter
# breaks the conformance test in ``tests/test_ports.py`` instead of silently
# disabling the recovery step behind a ``getattr`` probe.


@runtime_checkable
class SupportsRecovery(Protocol):
    """The adapter-side steps the engine's recovery and disclosure lean on."""

    @property
    def model(self) -> str:
        """The resolved primary model, compared with ``ProviderResult.model``
        to tell when a second model answered."""
        ...

    def input_budget(self) -> int | None:
        """The model's usable prompt window, or None when the adapter cannot say."""
        ...

    def sends_response_format(self, model: str) -> bool:
        """Whether calls to ``model`` are still sending ``response_format``."""
        ...

    def drop_response_format(self, model: str, why: str) -> None:
        """Step ``model``'s structured output down a rung from now on."""
        ...

    def lower_reasoning_effort(self) -> dict[str, Any] | None:
        """Per-call opts one effort level down, or None when there is no lower level."""
        ...

    def disable_reasoning(self) -> dict[str, Any] | None:
        """Per-call opts with reasoning off, or None when it cannot switch."""
        ...

    def escalate_model(self) -> str | None:
        """The resolved fallback model, or None when none is configured."""
        ...


class ReviewEngine(Protocol):
    """Port: turn a PR context + config into findings and a summary."""

    def review(self, ctx: PRContext, cfg: ReviewConfig) -> tuple[list[ReviewFinding], str]:
        """Produce (findings, summary) for the given PR and config."""
        ...


# Back-compat alias: the port was GitHub-only before lgtmaybe reviewed more than
# one forge. Kept so an out-of-tree adapter typed against the old name still
# imports; new code should use ``ReviewGateway``.
GitHubGateway = ReviewGateway
