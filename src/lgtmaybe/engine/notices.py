"""What the review summary says: its notices, its summary line, its hidden markers.

Split out of ``engine.py``: everything here is a pure function of facts the
run already recorded (:class:`_NoticeState`), so the wording of every notice
lives in one module and can be tested without running a review.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from lgtmaybe.core.comment import code_span
from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import PRStack, ReviewConfig, RiskAssessment
from lgtmaybe.core.version import package_version

from .lenses import _Lens
from .risk import risk_marker, risk_segment
from .run import _BUDGET_SKIP_REASON
from .stages import _SPEC_BUDGET_DIVISOR

# The engine's logger; see the note in run.py.
_log = get_logger("lgtmaybe.engine.engine")

# Hidden flag on the summary of a run that did not complete every lens call —
# a failed call, or one skipped past the max_review_seconds deadline (which
# reports itself as a failed call). Invisible in rendered Markdown, so it costs
# the review body nothing, and machine-readable, so the posting step can make
# the incompleteness *visible on the PR* rather than leaving it in a body edit
# nobody is notified about (see review.run_review). Its own marker family:
# deliberately disjoint from the summary/finding/reviewed markers the GitHub
# adapter matches on.
INCOMPLETE_MARKER = "<!-- lgtmaybe-incomplete -->"

# Which lenses completed every call they were given, stamped into the summary as
# a hidden comma-separated list (`<!-- lgtmaybe-lenses:artefacts,code-health,… -->`).
# A posting gate that wants "every lens ran" can require the set it expects
# rather than infer completeness from the ABSENCE of a failure notice — a lens
# that never ran leaves no notice to find. Its own marker family, disjoint from
# the summary/finding/reviewed/incomplete markers, like the one above.
LENSES_MARKER_PREFIX = "<!-- lgtmaybe-lenses:"


def _plural(n: int, one: str = "", many: str = "s") -> str:
    """The word (or suffix) for a count of *n*: *one* when it is exactly 1, else *many*."""
    return one if n == 1 else many


@dataclass(frozen=True)
class _NoticeState:
    cfg: ReviewConfig
    capped_files: bool
    total_files: int
    oversized: list[str]
    skipped_by_triage: list[str]
    errors: list[str]
    total_calls: int
    failed_calls: int
    failed_lenses: list[str]
    split_batches: int
    stepped_down: list[str]
    escalated: dict[str, str]
    repaired: list[str]
    re_asked: list[str]
    schema_dropped: bool
    reflection_skipped: str | None
    flooded: dict[str, int]
    suppressed: int
    off_diff: int
    open_finding_threads: int
    # Static-analysis hints on files no batch holds (lockfiles) that found no
    # security or correctness call to carry them.
    unbatched_hints_dropped: int = 0
    # Findings dropped per lens because their prose was the model's note to
    # itself about its output format (see selftalk.py).
    self_talk: dict[str, int] = field(default_factory=dict)
    # Lenses that answered only once reasoning was switched off — the rung below
    # `stepped_down`, and a different claim about where the findings came from.
    reasoning_off: list[str] = field(default_factory=list)
    # Matched spec roots none of whose files fit the spec budget, and whether
    # the spec lens ran at all (against the specs that did fit).
    spec_unfit: list[str] = field(default_factory=list)
    spec_reviewed: bool = False
    # The reflection audit's diff had to be trimmed to fit max_input_tokens.
    reflection_trimmed: bool = False


def _build_notices(state: _NoticeState) -> list[str]:
    notices: list[str] = []
    cfg = state.cfg
    if state.capped_files:
        notices.append(
            f"⚠️ Reviewed the top {cfg.max_files} of {state.total_files} changed files "
            f"(file cap {cfg.max_files}). Raise max_files to review them all."
        )
    if state.oversized:
        listed = ", ".join(code_span(path) for path in state.oversized[:10])
        more = ", …" if len(state.oversized) > 10 else ""
        notices.append(
            f"📄 Skipped {len(state.oversized)} oversized file{_plural(len(state.oversized))} "
            f"(over {cfg.max_file_diff_lines} diff lines): {listed}{more}. "
            "Raise `max_file_diff_lines` (0 disables) to review them."
        )
    if state.skipped_by_triage:
        listed = ", ".join(code_span(path) for path in state.skipped_by_triage[:10])
        more = ", …" if len(state.skipped_by_triage) > 10 else ""
        notices.append(
            f"🔎 Triage skipped {len(state.skipped_by_triage)} low-risk "
            f"file{_plural(len(state.skipped_by_triage))}: {listed}{more} "
            "(`/review full` reviews everything)."
        )
    if state.spec_unfit:
        listed = ", ".join(code_span(root) for root in state.spec_unfit)
        count = len(state.spec_unfit)
        budget = cfg.max_input_tokens // _SPEC_BUDGET_DIVISOR
        outcome = (
            "the spec lens judged the diff against the rest only"
            if state.spec_reviewed
            else "the spec lens was skipped, so the diff was not judged against "
            + _plural(count, "it", "them")
        )
        notices.append(
            f"📐 {listed} match{_plural(count, 'es', '')} this PR but none of "
            f"{_plural(count, 'its', 'their')} files fit the spec budget ({budget} tokens, "
            f"an eighth of `max_input_tokens`), so {outcome}. Raise `max_input_tokens` "
            "or split the spec into smaller files to review against it."
        )
    budget_skips = state.errors.count(_BUDGET_SKIP_REASON)
    if budget_skips:
        notices.append(
            f"💸 Token budget reached ({cfg.max_review_tokens} billable tokens): "
            f"{budget_skips} of {state.total_calls} review call"
            f"{_plural(budget_skips, ' was', 's were')} skipped, so this review is partial. "
            "Raise `max_review_tokens`, or spend less per run (a `triage_model`, fewer "
            "`categories`, lower `context_lines`)."
        )
    if state.failed_calls:
        # The MOST COMMON error, not the last one. Three lenses returning prose
        # and a fourth hitting a rate limit is a schema problem, but the last
        # error names the rate limit and sends the reader to the wrong knob —
        # and a wave that fails the same way N times is exactly the shape worth
        # reporting. Identical to the old behaviour when there is one error.
        detail = (
            Counter(state.errors).most_common(1)[0][0]
            if state.errors
            else "timeout or unparseable output"
        )
        lost = ", ".join(sorted(set(state.failed_lenses)))
        which = f"{lost}: " if lost else ""
        notices.append(
            f"⚠️ {state.failed_calls} of {state.total_calls} review calls failed "
            f"({which}{detail}); results may be incomplete.\n{INCOMPLETE_MARKER}"
        )
    if state.repaired:
        count = len(state.repaired)
        listed = ", ".join(f"`{lens}`" for lens in state.repaired)
        notices.append(
            f"🔧 {count} lens{_plural(count, many='es')} returned output that did not "
            f"parse and was reformatted by a second call ({listed}). Those findings are "
            "complete, but a model that keeps doing this is not honouring the output "
            "schema, which costs an extra call every time. Check the provider log for a "
            "dropped `response_format`, or try a different model."
        )
    if state.re_asked:
        count = len(state.re_asked)
        listed = ", ".join(f"`{lens}`" for lens in state.re_asked)
        notices.append(
            f"🧷 {count} lens{_plural(count, many='es')} returned output that did not parse "
            f"under the provider's JSON schema and answered when re-asked without the schema "
            f"({listed}). Those findings are complete, but this model's structured-output "
            "mode is producing replies lgtmaybe cannot read, so every affected lens costs two "
            "wasted calls before the one that works. Set `structured_output: false` to skip "
            "them, or use a model whose JSON mode works."
        )
    if state.failed_calls and state.schema_dropped:
        notices.append(f"🧩 {_SCHEMA_DROP_NOTE}")
    if state.split_batches:
        plural = _plural(state.split_batches, many="es")
        was = _plural(state.split_batches, "was", "were")
        notices.append(
            f"⏱️ {state.split_batches} batch{plural} {was} too big for one call "
            "(timed out, or ran past the `max_tokens` ceiling) and "
            f"{was} reviewed in smaller pieces instead. Consider a lower "
            "`max_input_tokens`, a higher `max_tokens`, or a faster model."
        )
    if state.stepped_down:
        count = len(state.stepped_down)
        listed = ", ".join(f"`{lens}`" for lens in state.stepped_down)
        notices.append(
            f"🧠 {count} lens{_plural(count, many='es')} spent its whole `max_tokens` "
            f"ceiling on reasoning and was re-run once at a lower `reasoning_effort` "
            f"({listed}). Those findings come from the lower setting. Lower "
            "`reasoning_effort` yourself, or raise `max_tokens`, to make that the "
            "first attempt rather than the second."
        )
    if state.reasoning_off:
        count = len(state.reasoning_off)
        listed = ", ".join(f"`{lens}`" for lens in state.reasoning_off)
        notices.append(
            f"🧠 {count} lens{_plural(count, many='es')} spent its whole `max_tokens` "
            f"ceiling on reasoning at a lower `reasoning_effort` too, and was re-run "
            f"once with reasoning switched off ({listed}). Those findings come from "
            "a run without thinking: this model treats `reasoning_effort` as a "
            "switch, so set it to `none` yourself, or raise `max_tokens`, to make "
            "that the first attempt rather than the third."
        )
    if state.escalated:
        count = len(state.escalated)
        listed = ", ".join(f"`{lens}`" for lens in sorted(state.escalated))
        models = ", ".join(f"`{model}`" for model in sorted(set(state.escalated.values())))
        notices.append(
            f"\u2934\ufe0f {count} lens{_plural(count, many='es')} could not be answered by "
            f"`{cfg.model}` and {_plural(count, 'was', 'were')} re-run on the fallback model "
            f"{models} ({listed}). Those findings are complete, but the review only finished "
            "because a second model paid for it, and a lens that keeps needing the fallback is "
            "saying it should be the primary."
        )
    if state.flooded:
        listed = ", ".join(
            f"`{lens}` ({dropped} dropped)" for lens, dropped in sorted(state.flooded.items())
        )
        notices.append(
            f"⚠️ Bounded a lens to the top {cfg.max_findings_per_lens} findings by severity: "
            f"{listed}. A lens returning many more findings than this is usually restating "
            f"one claim across many lines. Raise max_findings_per_lens to keep them all."
        )
    if state.self_talk:
        count = sum(state.self_talk.values())
        listed = ", ".join(f"`{lens}` ({n})" for lens, n in sorted(state.self_talk.items()))
        notices.append(
            f"🗑️ {count} finding{_plural(count)} {_plural(count, 'was', 'were')} dropped because "
            f"the model's reply carried its own repair note inside {_plural(count, 'it', 'them')} "
            '("discard the corrupted output… output valid JSON only"): '
            f"{listed}. That is the model talking to itself, not a review, and posting it would "
            "put an instruction in front of everyone who reads this thread; whatever genuine "
            "finding it was wrapped around is lost with it. A model that keeps doing this is "
            "unstable on this workload. Try a different model."
        )
    if state.reflection_skipped:
        notices.append(
            f"⚠️ {state.reflection_skipped}: the self-reflection audit was skipped, "
            "so findings may include false positives."
        )
    if state.reflection_trimmed:
        notices.append(
            "✂️ The self-reflection audit ran on a trimmed diff: the full diff "
            f"exceeded `max_input_tokens` ({cfg.max_input_tokens:,}), so only the "
            "flagged files' patches that fit were kept, in diff order. Flagged "
            "patches too large to fit were skipped. Raise `max_input_tokens` to "
            "audit the whole diff."
        )

    count_notices: tuple[tuple[int, Callable[[int], str]], ...] = (
        (
            state.suppressed,
            lambda count: (
                f"🙈 {count} finding{_plural(count)} suppressed (ignored fingerprint, "
                "inline `lgtmaybe: ignore`, or a 👎 from a previous run), not counted below."
            ),
        ),
        (
            state.off_diff,
            lambda count: (
                f"🔍 {count} scan finding{_plural(count)} skipped: on unchanged "
                "lines outside this PR's diff. Run the tool over the repository to see them."
            ),
        ),
        (
            state.unbatched_hints_dropped,
            lambda count: (
                f"🧰 {count} static-analysis hint{_plural(count)} skipped: "
                f"{_plural(count, 'it is', 'they are')} on dependency lockfiles or manifests "
                "the diff does not show, and no security or correctness review call ran to "
                "read them. Add `security` to `categories`, or set the tool to `finding` in "
                "`static_analysis.tool_mode` to post them directly."
            ),
        ),
        (
            state.open_finding_threads,
            lambda count: (
                f"💬 {count} earlier lgtmaybe "
                f"{_plural(count, 'conversation is', 'conversations are')} still unresolved on "
                "this PR. This run's count covers what it reviewed now, not those."
            ),
        ),
    )
    notices.extend(render(count) for count, render in count_notices if count)
    return notices


def _summary_line(
    count: int,
    cfg: ReviewConfig,
    risk: RiskAssessment | None = None,
    *,
    temperature_dropped: bool = False,
    stack: PRStack | None = None,
) -> str:
    """The review summary line: the user's template, or the built-in default.

    A template that fails to format (unknown placeholder, stray brace) is
    logged and falls back to the default — a cosmetic option must never fail
    a review. ``{risk}`` renders the risk-of-change segment (empty when off);
    ``{temperature}`` likewise renders ``temperature not sent`` when the
    configured temperature never reached the model, and nothing otherwise.
    ``{stack}`` renders the PR's layer in a stack of pull requests ("layer 2 of
    3 in a stack"), and nothing outside a stack.
    """
    version = package_version()
    segment = risk_segment(risk) if risk is not None else ""
    stack_segment = _stack_segment(stack)
    temperature = _TEMPERATURE_SEGMENT if temperature_dropped else ""
    if cfg.summary_template:
        try:
            return cfg.summary_template.format(
                count=count,
                provider=cfg.provider.value,
                model=cfg.model,
                version=version,
                risk=segment,
                stack=stack_segment,
                temperature=temperature,
            )
        except (KeyError, IndexError, ValueError) as exc:
            _log.warning(
                "summary_template failed to format — using the default",
                extra={"error": str(exc)},
            )
    plural = _plural(count)
    risk_part = f" · {segment}" if segment else ""
    stack_part = f" · {stack_segment}" if stack_segment else ""
    temperature_part = f" · {temperature}" if temperature else ""
    return (
        f"{count} finding{plural}{risk_part}{stack_part} · provider {cfg.provider} · "
        f"model {cfg.model}{temperature_part} · lgtmaybe {version}"
    )


def _stack_segment(stack: PRStack | None) -> str:
    """The summary's stack segment: an exact position only when both ends were found."""
    if stack is None:
        return ""
    if stack.complete:
        return f"layer {stack.position} of {stack.size} in a stack"
    return f"in a stack of at least {stack.size}"


def _lenses_marker(lenses: Sequence[_Lens], failed_lenses: Sequence[str]) -> str:
    """The hidden marker naming every lens that completed all of its calls.

    A lens with any failed or skipped call is left out: the failure notice
    already names it, and a gate reading this marker wants the lenses whose
    verdict it can rely on, not the ones that were merely attempted.
    """
    completed = sorted({lens.id for lens in lenses} - set(failed_lenses))
    return f"{LENSES_MARKER_PREFIX}{','.join(completed)} -->"


# Shown only alongside a failure, because on its own a dropped schema explains
# nothing — plenty of models parse fine without it. Next to "every call returned
# prose" it is very likely the cause, and it is the one part of that story the
# reader cannot see from the PR.
_SCHEMA_DROP_NOTE = (
    "The model refused the structured-output schema partway through this run, so "
    "later calls asked for JSON in the prompt only. That is a likely cause of the "
    "failures above. The provider log names which model and why."
)

# A summary-line segment beside the model, never a notice: unlike a dropped
# schema it explains nothing that went wrong, it qualifies the result — the
# `temperature: 0.0` default is what makes a review reproducible, and litellm's
# `drop_params` takes it away without a word on routes that refuse sampling
# params. As a notice it would cost the LGTM on every clean review of a common
# setup (gpt-5.x with reasoning on); the provider log names the model and why.
_TEMPERATURE_SEGMENT = "temperature not sent"


def _compose_summary(
    notices: Sequence[str],
    summary_line: str,
    *,
    clean: bool,
    lenses: Sequence[_Lens],
    failed_lenses: Sequence[str],
    risk: RiskAssessment | None,
) -> str:
    """The posted summary: the notices, the summary line, then the hidden markers.

    *clean* is whether the review returned no findings at all; it earns the
    explicit thumbs-up only when no notice qualifies it.
    """
    # Which lenses completed, on every summary shape — hidden, so it costs
    # the rendered body nothing and a terminal never prints it.
    lenses_marker = _lenses_marker(lenses, failed_lenses)
    # The risk marker rides just above it, so the lenses marker still closes
    # every summary. No verdict (off, or unavailable) means no marker at all.
    if risk is not None and (marker := risk_marker(risk)):
        lenses_marker = f"{marker}\n{lenses_marker}"
    if notices:
        return "\n\n".join([*notices, summary_line]) + f"\n{lenses_marker}"
    # A genuinely clean review (nothing flagged, every call succeeded) gets an
    # explicit thumbs-up rather than a bare "0 findings".
    if clean:
        return f"👍 LGTM!\n\n{summary_line}\n{lenses_marker}"
    return f"{summary_line}\n{lenses_marker}"
