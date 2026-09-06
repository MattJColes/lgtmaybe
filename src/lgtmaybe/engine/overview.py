"""The change overview: one comment answering three questions about a PR.

What is this change (the description), what is risky about it (High Impact
Areas), and what does it touch and in what order (the diagrams). Each is its
own focused model call — one prompt doing three jobs degrades all three — and
they run concurrently, so the overview costs about as much wall-clock as its
slowest call rather than the sum of them.

The two added sections are best-effort: either failing renders a visible
"unavailable" line and the comment still posts. The diagram call keeps the
failure semantics it always had, because an automatic diagram is a required
completion step — swallowing its failure would stamp a head complete with no
diagram on it.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from lgtmaybe.core.logging import get_logger
from lgtmaybe.core.models import DiagramResult, PRContext, ReviewConfig
from lgtmaybe.core.ports import ProviderClient

from .describe import (
    describe_result,
    markdown_text,
    render_description_detail,
    render_description_head,
)
from .diagram import (
    DIAGRAM_INVALID_NOTICE,
    diagram_result,
    render_diagram_comment,
    render_diagram_views,
)
from .high_impact import build_high_impact

_T = TypeVar("_T")

_log = get_logger(__name__)

_DESCRIPTION_UNAVAILABLE = "_Description unavailable — the model returned no usable description._"

_OVERVIEW_TITLE = "Change overview"


def build_overview(ctx: PRContext, cfg: ReviewConfig, provider: ProviderClient) -> str:
    """Build the change-overview comment body from up to three concurrent calls."""
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="lgtmaybe-overview") as pool:
        described = (
            pool.submit(_safe, lambda: describe_result(ctx, cfg, provider), "describe")
            if cfg.auto_describe
            else None
        )
        impact = pool.submit(build_high_impact, ctx, cfg, provider) if cfg.high_impact else None
        diagram = pool.submit(diagram_result, ctx, cfg, provider)

        # The diagram's failure is the one that propagates — an automatic
        # diagram is a required completion step, so it must be able to fail the
        # run. The other two were already made safe on the way in.
        graph = diagram.result()
        desc, has_intent = (
            described.result() or (None, False)
            if described is not None
            else (
                None,
                False,
            )
        )
        high_impact = impact.result() if impact is not None else None

    # Whole-body fast path: with both sections off there is nothing to compose,
    # and the overview must stay byte-identical to the standalone diagram.
    if not cfg.auto_describe and high_impact is None:
        return render_diagram_comment(graph)

    sections: list[str] = []
    if cfg.auto_describe:
        sections.append(
            render_description_head(desc) if desc is not None else _fallback_head(graph)
        )
    elif graph is not None:
        sections.append(_diagram_head(graph))

    if high_impact is not None:
        sections.append(high_impact)
    if desc is not None:
        sections.append(render_description_detail(desc, has_intent=has_intent))

    views = None if graph is None else render_diagram_views(graph, headed=True)
    sections.append(views if views is not None else DIAGRAM_INVALID_NOTICE)
    return "\n\n".join(section for section in sections if section)


def _diagram_head(graph: DiagramResult) -> str:
    """The diagram's own title and summary, used when no description heads the comment."""
    title = markdown_text(graph.title) or _OVERVIEW_TITLE
    summary = markdown_text(graph.summary)
    return f"## {title}\n\n{summary}" if summary else f"## {title}"


def _fallback_head(graph: DiagramResult | None) -> str:
    """Head the comment when the description call gave nothing usable.

    The slot is filled visibly rather than left out: a missing description that
    looks like a design choice hides that a call failed.
    """
    head = _diagram_head(graph) if graph is not None else f"## {_OVERVIEW_TITLE}"
    return f"{head}\n\n{_DESCRIPTION_UNAVAILABLE}"


def _safe(call: Callable[[], _T], label: str) -> _T | None:
    """Run a best-effort section's call, logging and swallowing any failure."""
    try:
        return call()
    except Exception:  # noqa: BLE001 — a section failing must not lose the comment
        _log.warning("%s call failed — the overview continues without it", label, exc_info=True)
        return None
