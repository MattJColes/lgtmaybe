"""The lens set one review fans out over: built-ins per the preset, then custom lenses.

Split out of ``engine.py`` so every module that speaks about a lens (the run,
the preparation stages, the recovery ladder, the summary notices) can name the
type without importing the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import (
    ReviewCategory,
    ReviewConfig,
    ReviewPreset,
    StaticAnalysisTool,
    ToolMode,
)

from .prompt import (
    FAST_GROUPS,
    build_correctness_block,
    build_custom_lens_block,
    build_group_block,
    build_lens_block,
)
from .static_analysis import mode_for

# The engine's logger; see the note in run.py.
_log = get_logger("lgtmaybe.engine.engine")


@dataclass(frozen=True)
class _Lens:
    """One review lens in the fan-out: a built-in category or a user-defined lens.

    ``user_block`` is the split layout's final user message, keeping the lens
    checklist outside the shared preamble and diff prefix.
    ``carries_intent`` is true only for the built-in intent lens, the one call
    that receives the stated-intent block; ``carries_spec`` likewise for the spec
    lens and the committed-specification block.
    """

    id: str
    user_block: str
    carries_intent: bool = False
    carries_spec: bool = False
    # For a merged (fast-preset) lens: the category values the model may stamp
    # on its findings. The engine keeps a model-supplied category in this set
    # and falls back to the lens id otherwise; None (a focused lens) means the
    # lens id is always stamped — the model's value is ignored, as before.
    allowed_categories: frozenset[str] | None = None


def _build_lenses(cfg: ReviewConfig, *, has_intent: bool, has_spec: bool = False) -> list[_Lens]:
    """All lenses to run: built-ins (grouped per the preset), then user lenses.

    The fast preset runs the ten everyday built-ins as FIVE distinct lenses —
    security, correctness, code health, artefacts, architecture — one per
    concern, the same set on every provider. The lens set is a property of the
    preset, not of how many workers happen to be available: a single-worker
    provider runs the same five calls, serially. This grouping applies only
    when ``categories`` is the untouched default: a user who explicitly listed
    lenses asked for exactly those, so the preset never regroups them. The full
    preset runs every category; an explicit list runs exactly its selected
    categories. Both skip intent when nothing states an intent, and skip spec
    when no committed specification matches the PR.

    Spec is the one built-in that does not fold into a fast-preset group. It
    carries a large block of its own (requirements, design, task list) and under
    ``fast`` the correctness lens already carries the stated intent; stacking
    both on one call degrades it. So a matched spec adds a SIXTH call — paid only
    in repositories that commit a spec the PR is delivering.

    """
    # Whether the model should still be asked for dependency-advisory claims.
    # Config-derived on purpose: keying this on whether the binary happens to be
    # installed would make the prompt — and the shared prefix cache — vary by
    # machine. A configured-but-missing finding-mode tool warns instead.
    deps = not _scanner_covers(cfg, StaticAnalysisTool.osv_scanner)
    # Likewise for committed secrets: redaction has already rewritten every
    # secret it matched to `[REDACTED]` before the diff leaves, so a scanner
    # reading the unredacted text answers this far better than the lens can.
    secrets = not _scanner_covers(cfg, StaticAnalysisTool.gitleaks)
    fast = cfg.preset is ReviewPreset.fast and list(cfg.categories) == list(ReviewCategory)
    if fast:
        lenses = [
            _Lens(
                id=ReviewCategory.security.value,
                user_block=build_lens_block(ReviewCategory.security, secret_scanning=secrets),
            ),
        ]
        lenses.append(
            _Lens(
                id=ReviewCategory.correctness.value,
                user_block=build_correctness_block(has_intent),
                carries_intent=has_intent,
                allowed_categories=(
                    frozenset({ReviewCategory.correctness.value, ReviewCategory.intent.value})
                    if has_intent
                    else None
                ),
            )
        )
        lenses += [
            _Lens(
                id=group.id,
                user_block=build_group_block(group, dependency_health=deps),
                allowed_categories=frozenset(c.value for c in group.members),
            )
            for group in FAST_GROUPS
        ]
        # Architecture is its own call, not a code-health member: that group
        # already carries four concerns, and design findings would be crowded
        # out by the cheaper performance and complexity nits beside them.
        lenses.append(
            _Lens(
                id=ReviewCategory.architecture.value,
                user_block=build_lens_block(ReviewCategory.architecture),
            )
        )
        if has_spec:
            lenses.append(_spec_lens())
    else:
        lenses = [
            _Lens(
                id=category.value,
                user_block=build_lens_block(
                    category, dependency_health=deps, secret_scanning=secrets
                ),
                carries_intent=category is ReviewCategory.intent,
                carries_spec=category is ReviewCategory.spec,
            )
            for category in cfg.categories
        ]
        if not has_intent and any(lens.carries_intent for lens in lenses):
            lenses = [lens for lens in lenses if not lens.carries_intent]
            _log.info("intent lens skipped — no stated intent (title/description/commits)")
        if not has_spec and any(lens.carries_spec for lens in lenses):
            lenses = [lens for lens in lenses if not lens.carries_spec]
            _log.info("spec lens skipped — no committed specification matches this PR")
    lenses += [
        _Lens(
            id=lens.id,
            user_block=build_custom_lens_block(lens),
        )
        for lens in cfg.extra_lenses
    ]
    return lenses


def _spec_lens() -> _Lens:
    """The spec lens, built the same way in either preset."""
    return _Lens(
        id=ReviewCategory.spec.value,
        user_block=build_lens_block(ReviewCategory.spec),
        carries_spec=True,
    )


def _scanner_covers(cfg: ReviewConfig, tool: StaticAnalysisTool) -> bool:
    """Whether *tool* will report its own findings on this run.

    When one will, the lens stops being asked for what it covers. For
    ``osv_scanner`` that is dependency advisories: a model's knowledge cutoff
    cannot answer "does this version have a published advisory?", so asking
    anyway only puts a confident guess beside an accurate answer. For
    ``gitleaks`` it is committed secrets: redaction rewrites every secret it
    matches to ``[REDACTED]`` before the diff is sent, so the model is largely
    being asked to find what it has been prevented from seeing — while gitleaks
    reads the unredacted head text and answers it exactly.
    """
    sa = cfg.static_analysis
    return sa.enabled and tool in sa.tools and mode_for(tool, cfg) is ToolMode.finding
