"""Spec-driven development: find the committed spec a PR claims to deliver.

Three workflows commit their specifications into the repository — **OpenSpec**
(``openspec/``), **GitHub Spec Kit** (``.specify/`` plus ``specs/NNN-slug/``) and
**Kiro** (``.kiro/specs/<feature>/``). All three write requirements, a design,
and a ``tasks.md`` checklist, which makes the spec a far better statement of
intent than a PR description: it is structured, it predates the code, and its
checkboxes record what the author claims to have finished.

This module is the spec lens's deterministic half, and it does four things:

- :func:`detect` probes the workspace for a known layout. Nothing found means
  the lens never runs, so a repo without specs pays nothing.
- :func:`select` ranks the candidate spec directories against the PR, because a
  monorepo may hold forty of them and only one is the subject of this change.
- :func:`load_spec_files` reads the selected files, preferring the PR's own head
  text for a spec the PR itself changes (see the fork-safety note below).
- :func:`ticked_tasks` mines the diff for checkboxes the PR flips from ``[ ]`` to
  ``[x]``. Those are the author's explicit delivery claims, extracted without a
  model call and without reading anything the diff does not already carry.

**Fork safety.** Spec text reaches the model as untrusted data
(:func:`~lgtmaybe.engine.injection.wrap_spec`), exactly like the stated intent,
and for the same reason: on a fork PR part of it is attacker-controlled. Files
the PR changes are read from its head text — a spec is usually committed in the
same PR that implements it, so reading only the trusted base branch would miss
the very requirements being delivered. Everything else is read from the
checked-out workspace (the base branch on ``pull_request_target``) through the
retrieval budget, which redacts secrets and caps the token spend. The worst a
planted spec can do is suppress a spec finding; no other lens sees this block.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from lgtmaybe.core.diffparse import walk_diff
from lgtmaybe.core.logging import get_logger

from . import retrieve

_log = get_logger(__name__)

# At most two spec directories reach the model. Ranking is what makes this safe:
# a PR that touches its own spec, or runs on a branch named after one, scores far
# above the rest, and sending "the top few" instead would spend a large slice of
# the budget re-stating specs nobody is delivering.
MAX_SELECTED = 2

# The whole spec block is capped at this many files across all selected bundles.
MAX_SPEC_FILES = 8

# Spec files in the order a reader needs them: what is required, then how it was
# designed, then what was planned. One order serves all three systems — Spec Kit
# has no requirements.md, Kiro no spec.md — so no per-system table is needed.
_SPEC_FILE_ORDER = (
    "requirements.md",
    "spec.md",
    "proposal.md",
    "design.md",
    "plan.md",
    "tasks.md",
)

# The checklist file every one of the three systems writes. Checkboxes anywhere
# else — a PR template, a README feature list — are not delivery claims.
_TASKS_FILENAME = "tasks.md"

# `- [x] T014 ...` / `  * [X] 3.2 ...`. The marker is what matters; the leading
# bullet and indentation vary by system and by nesting depth.
_TICKED_RE = re.compile(r"^\s*[-*]\s*\[[xX]\]\s+(?P<text>\S.*?)\s*$")

# A slug shorter than this matches too much prose to be evidence of anything.
_MIN_SLUG_MATCH_LEN = 3

# `003-payment-links` -> `payment-links`, so a branch named `feat/payment-links`
# still matches the spec its number prefixes.
_NUMERIC_PREFIX_RE = re.compile(r"^\d+[-_]")

_SCORE_PR_TOUCHES_SPEC = 4
_SCORE_BRANCH_NAMES_SPEC = 3
_SCORE_INTENT_NAMES_SPEC = 2
_SCORE_ACTIVE_CHANGE = 1


class SpecSystem(StrEnum):
    """A spec-driven workflow whose specs live in the repository."""

    openspec = "openspec"
    speckit = "speckit"
    kiro = "kiro"


@dataclass(frozen=True)
class SpecBundle:
    """One spec directory: the unit a PR delivers, and the unit selection ranks.

    ``root`` and ``files`` are repo-relative POSIX paths. ``slug`` is the
    directory's own name — the handle that shows up in branch names, commit
    subjects and PR titles, which is what makes it worth matching on.
    """

    system: SpecSystem
    slug: str
    root: str
    files: tuple[str, ...]


class _Tree:
    """The repository as detection sees it: the workspace UNION the PR's own paths.

    Spec-driven work commits the spec in the pull request that implements it, so
    on the first PR of a feature the spec directory does not exist on the base
    branch — which is the only thing the workspace holds on
    ``pull_request_target``. Detection that walked the filesystem alone would
    therefore skip the lens in precisely the case the spec is newest and the
    review most needs it.

    Adding the PR's paths here rather than in a second detection pass keeps ONE
    set of layout rules: the ``specs/`` guard, the archive exclusion and the
    reading order cannot drift between a spec that already existed and one this
    PR introduces. Existence only — no content is read, and the paths are never
    resolved against the filesystem, so a hostile path cannot escape the root.
    """

    def __init__(self, root: Path, changed_files: Sequence[str] = ()) -> None:
        self._root = root
        self._added = {p for p in changed_files if p}
        self._added_dirs = {
            str(parent)
            for p in self._added
            for parent in PurePosixPath(p).parents
            if str(parent) != "."
        }

    def is_file(self, rel: str) -> bool:
        return rel in self._added or (self._root / rel).is_file()

    def is_dir(self, rel: str) -> bool:
        return rel in self._added_dirs or (self._root / rel).is_dir()

    def dirs_in(self, rel: str) -> list[str]:
        """Immediate subdirectory names of *rel*, sorted, from disk and the PR."""
        names = {
            PurePosixPath(d).name for d in self._added_dirs if str(PurePosixPath(d).parent) == rel
        }
        parent = self._root / rel
        if parent.is_dir():
            names |= {child.name for child in parent.iterdir() if child.is_dir()}
        return sorted(names)

    def spec_files_in(self, rel_root: str) -> tuple[str, ...]:
        """The known spec files directly inside *rel_root*, in reading order."""
        return tuple(
            f"{rel_root}/{name}" for name in _SPEC_FILE_ORDER if self.is_file(f"{rel_root}/{name}")
        )

    def glob_dirs(self, pattern: str) -> list[str]:
        """Directories matching a ``spec_paths`` glob, from disk and the PR."""
        found = {
            d for d in self._added_dirs if fnmatchcase(d, pattern) or fnmatchcase(f"{d}/", pattern)
        }
        for match in self._root.glob(pattern):
            if not match.is_dir():
                continue
            try:
                found.add(match.relative_to(self._root).as_posix())
            except ValueError:  # a pattern that climbed out of the workspace
                continue
        return sorted(found)


def _bundle(
    tree: _Tree, system: SpecSystem, rel_root: str, extra: Sequence[str] = ()
) -> SpecBundle | None:
    """Build a bundle for *rel_root*, or None when it holds no spec files."""
    files = tree.spec_files_in(rel_root) + tuple(extra)
    if not files:
        return None
    return SpecBundle(system=system, slug=PurePosixPath(rel_root).name, root=rel_root, files=files)


def _detect_openspec(tree: _Tree) -> list[SpecBundle]:
    """Active change proposals and living capability specs under ``openspec/``.

    Archived changes are deliberately excluded: they describe work that already
    shipped, so holding a PR to one would be judging it against history.
    """
    if not tree.is_dir("openspec"):
        return []

    bundles: list[SpecBundle] = []
    for name in tree.dirs_in("openspec/changes"):
        if name == "archive":
            continue
        rel = f"openspec/changes/{name}"
        # Delta specs sit one level down, one directory per capability.
        deltas = tuple(
            f"{rel}/specs/{cap}/spec.md"
            for cap in tree.dirs_in(f"{rel}/specs")
            if tree.is_file(f"{rel}/specs/{cap}/spec.md")
        )
        bundle = _bundle(tree, SpecSystem.openspec, rel, deltas)
        if bundle is not None:
            bundles.append(bundle)

    for capability in tree.dirs_in("openspec/specs"):
        bundle = _bundle(tree, SpecSystem.openspec, f"openspec/specs/{capability}")
        if bundle is not None:
            bundles.append(bundle)

    return bundles


def _detect_speckit(tree: _Tree) -> list[SpecBundle]:
    """Feature directories under ``specs/``, when this really is a Spec Kit tree.

    A bare ``specs/`` folder of prose is not Spec Kit, and treating it as one
    would fire the lens on every documentation repository. Either the
    ``.specify/`` scaffolding is present, or the directory carries both a spec
    and the plan Spec Kit generates from it. The guard applies to a spec the PR
    adds exactly as it does to one already on the branch.
    """
    scaffolded = tree.is_dir(".specify")
    bundles: list[SpecBundle] = []
    for feature in tree.dirs_in("specs"):
        rel = f"specs/{feature}"
        has_plan = tree.is_file(f"{rel}/plan.md") and tree.is_file(f"{rel}/spec.md")
        if not scaffolded and not has_plan:
            continue
        bundle = _bundle(tree, SpecSystem.speckit, rel)
        if bundle is not None:
            bundles.append(bundle)
    return bundles


def _detect_kiro(tree: _Tree) -> list[SpecBundle]:
    """Feature directories under ``.kiro/specs/``."""
    bundles: list[SpecBundle] = []
    for feature in tree.dirs_in(".kiro/specs"):
        bundle = _bundle(tree, SpecSystem.kiro, f".kiro/specs/{feature}")
        if bundle is not None:
            bundles.append(bundle)
    return bundles


def _detect_extra(tree: _Tree, patterns: Sequence[str]) -> list[SpecBundle]:
    """Spec directories named by ``ReviewConfig.spec_paths`` globs.

    The escape hatch for a house layout the three known systems do not describe.
    A match that holds no recognised spec file is skipped like any other.
    """
    bundles: list[SpecBundle] = []
    seen: set[str] = set()
    for pattern in patterns:
        for rel in tree.glob_dirs(pattern):
            if rel in seen:
                continue
            seen.add(rel)
            # No system owns a custom layout; label it by the closest match so
            # the prompt can still name what it is reading.
            bundle = _bundle(tree, _system_for(rel), rel)
            if bundle is not None:
                bundles.append(bundle)
    return bundles


def _system_for(rel: str) -> SpecSystem:
    if rel.startswith("openspec/"):
        return SpecSystem.openspec
    if rel.startswith(".kiro/"):
        return SpecSystem.kiro
    return SpecSystem.speckit


def detect(
    root: Path, extra_paths: Sequence[str] = (), changed_files: Sequence[str] = ()
) -> list[SpecBundle]:
    """Every spec directory in the workspace, across all known layouts.

    A pure filesystem probe — no file body is read and no model is called — so
    running it on every review costs a handful of ``stat`` calls. An empty
    result is the common case and means the spec lens never runs.
    """
    tree = _Tree(root, changed_files)
    bundles = (
        _detect_openspec(tree)
        + _detect_speckit(tree)
        + _detect_kiro(tree)
        + _detect_extra(tree, extra_paths)
    )
    if bundles:
        _log.info(
            "spec systems detected",
            extra={"count": len(bundles), "systems": sorted({b.system.value for b in bundles})},
        )
    return bundles


def _slug_variants(slug: str) -> list[str]:
    variants = [slug]
    stripped = _NUMERIC_PREFIX_RE.sub("", slug)
    if stripped and stripped != slug:
        variants.append(stripped)
    return variants


def _mentions(text: str, slug: str) -> bool:
    """Whether *text* names *slug* as a whole token.

    Token-bounded rather than a bare substring: `alpha` must not match
    `alphabetical`, and a two-character slug must not match at all.
    """
    for variant in _slug_variants(slug):
        if len(variant) < _MIN_SLUG_MATCH_LEN:
            continue
        if re.search(rf"(?<![0-9A-Za-z]){re.escape(variant)}(?![0-9A-Za-z])", text, re.I):
            return True
    return False


def _score(bundle: SpecBundle, changed_files: Sequence[str], branch: str, intent_text: str) -> int:
    score = 0
    prefix = f"{bundle.root}/"
    if any(path.startswith(prefix) for path in changed_files):
        score += _SCORE_PR_TOUCHES_SPEC
    if branch and _mentions(branch, bundle.slug):
        score += _SCORE_BRANCH_NAMES_SPEC
    if intent_text and _mentions(intent_text, bundle.slug):
        score += _SCORE_INTENT_NAMES_SPEC
    if bundle.root.startswith("openspec/changes/"):
        # An un-archived change proposal is work in flight by definition.
        score += _SCORE_ACTIVE_CHANGE
    return score


def select(
    bundles: Sequence[SpecBundle],
    *,
    changed_files: Sequence[str],
    branch: str,
    intent_text: str,
) -> list[SpecBundle]:
    """The spec directories this PR is plausibly delivering, best first.

    Ranked on evidence the PR itself supplies: it edits the spec, its branch is
    named after one, or its title/description/commits name one. When nothing
    scores, the answer is usually none — silence beats holding a change to a
    spec it never mentioned. The one exception is a repository with a **single**
    spec directory, where there is nothing else the PR could be delivering.
    """
    if not bundles:
        return []

    scored = [(_score(b, changed_files, branch, intent_text), b) for b in bundles]
    matched = [(score, b) for score, b in scored if score > 0]

    if not matched:
        if len(bundles) == 1:
            return list(bundles)
        _log.info("spec lens skipped — no spec matches this PR", extra={"candidates": len(bundles)})
        return []

    # Descending score, then slug, so an equal-scoring pair always resolves the
    # same way — the fan-out downstream must be reproducible run to run.
    matched.sort(key=lambda pair: (-pair[0], pair[1].slug))
    return [bundle for _, bundle in matched[:MAX_SELECTED]]


def ticked_tasks(diff: str) -> list[str]:
    """Task-list entries this PR flips to done — the author's delivery claims.

    Only ``+`` lines in a ``tasks.md`` count. A checkbox that was already ticked
    arrives as a context line and is not a claim about *this* change; a task
    added still unticked is not a claim at all; and a checklist in a README or a
    PR template is not a task list.

    This is the highest-precision signal the spec lens has, and it costs one
    pass over a diff the engine already holds.
    """
    claims: list[str] = []
    for path, kind, _old, _new, text in walk_diff(diff):
        if kind != "+" or PurePosixPath(path).name != _TASKS_FILENAME:
            continue
        match = _TICKED_RE.match(text)
        if match:
            claims.append(match.group("text"))
    return claims


def _reader(root: Path, head_texts: Mapping[str, str]) -> Callable[[str], str | None]:
    """Read a spec file: the PR's head text when it has one, else the workspace.

    Head text matters because spec-driven development commits the spec in the
    same PR that implements it — reading only the trusted base branch would hand
    the lens the version *before* the requirements it is meant to check. It is
    untrusted, which is why the rendered block is wrapped as untrusted data.
    """
    resolved_root = root.resolve()

    def read(path: str) -> str | None:
        head = head_texts.get(path)
        if head is not None:
            return head
        try:
            target = (resolved_root / path).resolve()
            if not target.is_relative_to(resolved_root):
                return None
            return target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    return read


def load_spec_files(
    bundles: Sequence[SpecBundle],
    *,
    root: Path,
    head_texts: Mapping[str, str],
    budget_tokens: int,
) -> dict[str, str]:
    """Read the selected bundles' files, redacted and inside the token budget.

    Goes through :func:`retrieve.resolve_needs` for the same reason directory
    context does: it redacts secrets, costs each file against a budget, caps the
    file count, and drops anything unreadable instead of failing the review.
    """
    wanted = [path for bundle in bundles for path in bundle.files]
    if not wanted:
        return {}
    return retrieve.resolve_needs(
        wanted,
        _reader(root, head_texts),
        already=set(),
        budget_tokens=max(1, budget_tokens),
        max_files=MAX_SPEC_FILES,
    )


_LEAD_IN = (
    "This repository drives its work from a committed specification, and the "
    "pull request under review appears to deliver the specification below. Judge "
    "the diff against it."
)

_CLAIMS_LEAD_IN = (
    "The diff itself ticks these task-list entries off as done. Each one is a "
    "claim the author made in this pull request — verify that the change "
    "actually delivers it:"
)


def build_spec_text(
    bundles: Sequence[SpecBundle],
    contents: Mapping[str, str],
    *,
    claims: Sequence[str],
) -> str | None:
    """Render the spec block's body, or None when there is nothing to judge against.

    Returns plain text: the caller redacts it once and wraps it per batch, the
    same split the stated intent uses, because the wrapper also has to name the
    files each individual call cannot see.

    Claims without any readable spec return None on purpose. A ticked checkbox
    alone tells the model what the author says they did but nothing about what
    was required, which is an invitation to guess.
    """
    if not contents:
        return None

    parts = [_LEAD_IN]
    for bundle in bundles:
        rendered = [
            f"--- {path} ---\n{contents[path]}" for path in bundle.files if path in contents
        ]
        if not rendered:
            continue
        parts.append(
            f"### {bundle.system.value} specification: {bundle.slug} ({bundle.root})\n\n"
            + "\n\n".join(rendered)
        )

    if len(parts) == 1:  # every selected bundle fell outside the budget
        return None

    if claims:
        parts.append(_CLAIMS_LEAD_IN + "\n" + "\n".join(f"- {claim}" for claim in claims))

    return "\n\n".join(parts)
