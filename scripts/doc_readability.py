"""Readability checks for the docs: the machine-checkable half of write-docs.md.

    python scripts/doc_readability.py                 # summary of every page
    python scripts/doc_readability.py docs/how-to/x.md  # every problem on a page

It reads a page's prose only (front matter, code, tables, HTML and headings are
dropped), splits it into sentences, and reports:

- a sentence over ``MAX_SENTENCE_WORDS`` words;
- an average sentence length over ``MAX_AVERAGE_WORDS`` words;
- an em dash (write " - " or split the sentence);
- a key project term (``KEY_TERMS``) used without a link to its glossary entry.

``tests/docs/test_readability.py`` runs it as a gate on every rewritten page.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_SENTENCE_WORDS = 35
MAX_AVERAGE_WORDS = 18

# Project jargon a newcomer meets first, mapped to its glossary anchor. A page
# that uses the term must link to the entry at least once.
KEY_TERMS: dict[str, str] = {
    r"\blens(?:es)?\b": "lens",
    r"\bpresets?\b": "preset",
    r"\bhunks?\b": "hunk",
    r"\b(?:self-)?reflection\b": "reflection",
    r"\bwatermarks?\b": "watermark",
    r"\bfan-out\b": "fan-out",
    r"\bincremental review\b": "incremental-review",
    r"\bforges?\b": "forge",
    r"\bredact(?:s|ed|ion)?\b": "redaction",
    r"\bhigh impact areas\b": "high-impact-areas",
}

_GLOSSARY = "docs/reference/glossary.md"

_FENCE = re.compile(r"^\s*(```|~~~)")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_SKIP_LINE = re.compile(
    r"^\s*(?:#|\||<|!\[|\*\[|\[[^\]]+\]:|!!!|\?\?\?|---+\s*$|\*\*\*+\s*$|=+\s*$)"
)
_INLINE_CODE = re.compile(r"`[^`]*`")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)(?:\{[^}]*\})?")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_REF_LINK = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_URL = re.compile(r"<?https?://\S+?>?(?=[\s)]|$)")
_ATTR = re.compile(r"\{[:#.][^}]*\}")
_EMPHASIS = re.compile(r"\*\*|__")
# Dots that do not end a sentence: abbreviations, decimals, file extensions.
_ABBREVIATIONS = re.compile(r"\b(?:e\.g|i\.e|etc|vs|approx|cf|incl)\.", re.IGNORECASE)
# A new sentence starts with a capital, a digit, a quote or code, or with one of
# the names these docs spell lowercase even at the start of a sentence.
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z0-9\"'(\[]|CODE|lgtmaybe\b|ollama\b)")
_WORD = re.compile(r"[A-Za-z0-9][\w'’./+:-]*")


@dataclass(frozen=True)
class Problem:
    kind: str
    detail: str
    path: str = ""

    def __str__(self) -> str:
        return f"{self.path}: [{self.kind}] {self.detail}"


def _strip_front_matter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2]
    return text


def _outside_fences(text: str) -> str:
    """The page with its fenced code blocks removed."""
    kept: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            kept.append(line)
    return "\n".join(kept)


def _clean(unit: str) -> str:
    unit = _IMAGE.sub("", unit)
    unit = _INLINE_CODE.sub("CODE", unit)
    unit = _LINK.sub(r"\1", unit)
    unit = _REF_LINK.sub(r"\1", unit)
    unit = _URL.sub("URL", unit)
    unit = _ATTR.sub("", unit)
    unit = _EMPHASIS.sub("", unit)
    return " ".join(unit.split())


def prose_units(text: str) -> list[str]:
    """The page's prose, as paragraphs and list items (one unit each)."""
    units: list[str] = []
    current: list[str] = []
    in_fence = False

    def flush() -> None:
        if current:
            cleaned = _clean(" ".join(current))
            if cleaned:
                units.append(cleaned)
            current.clear()

    for line in _strip_front_matter(text).splitlines():
        if _FENCE.match(line):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line.strip() or _SKIP_LINE.match(line):
            flush()
            continue
        if _LIST_ITEM.match(line):
            flush()
            current.append(_LIST_ITEM.sub("", line, count=1).strip())
            continue
        current.append(line.strip())
    flush()
    return units


def sentences(unit: str) -> list[str]:
    """Split one prose unit into sentences."""
    masked = _ABBREVIATIONS.sub(lambda m: m.group(0).replace(".", "\x00"), unit)
    parts = _SENTENCE_END.split(masked)
    return [p.replace("\x00", ".").strip() for p in parts if p.strip()]


def word_count(sentence: str) -> int:
    return len(_WORD.findall(sentence))


def check_page(text: str, *, path: str) -> list[Problem]:
    """Every readability problem on one page."""
    units = prose_units(text)
    all_sentences = [s for unit in units for s in sentences(unit)]
    problems: list[Problem] = []

    counts = [word_count(s) for s in all_sentences]
    for sentence, words in zip(all_sentences, counts, strict=True):
        if words > MAX_SENTENCE_WORDS:
            problems.append(Problem("long-sentence", f"{words} words: {sentence[:120]}", path))

    if counts:
        average = sum(counts) / len(counts)
        if average > MAX_AVERAGE_WORDS:
            problems.append(
                Problem(
                    "average",
                    f"{average:.1f} words per sentence (max {MAX_AVERAGE_WORDS})",
                    path,
                )
            )

    for unit in units:
        if "—" in unit:
            problems.append(Problem("em-dash", unit[:120], path))

    if path != _GLOSSARY:
        prose = " ".join(units)
        # A link inside a code example is not a link the reader can follow.
        linkable = _outside_fences(_strip_front_matter(text))
        for pattern, anchor in KEY_TERMS.items():
            used = re.search(pattern, prose, flags=re.IGNORECASE)
            # Not followed by "-": a link to #lens-pack is not a link to #lens.
            linked = re.search(rf"glossary\.md#{re.escape(anchor)}(?![-\w])", linkable)
            if used and not linked:
                problems.append(
                    Problem(
                        "glossary",
                        f"'{used.group(0)}' used without a link to glossary.md#{anchor}",
                        path,
                    )
                )
    return problems


def _summary(root: Path) -> int:
    docs = sorted(p.relative_to(root).as_posix() for p in (root / "docs").rglob("*.md"))
    pages = ["README.md", *docs]
    print(f"{'page':58} {'avg':>5} {'long':>5} {'dash':>5} {'gloss':>5}")
    for page in pages:
        text = (root / page).read_text(encoding="utf-8")
        units = prose_units(text)
        counts = [word_count(s) for u in units for s in sentences(u)]
        average = sum(counts) / len(counts) if counts else 0.0
        problems = check_page(text, path=page)
        kinds = ("long-sentence", "em-dash", "glossary")
        tally = {k: sum(p.kind == k for p in problems) for k in kinds}
        print(
            f"{page:58} {average:5.1f} {tally['long-sentence']:5} "
            f"{tally['em-dash']:5} {tally['glossary']:5}"
        )
    return 0


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    if not argv:
        return _summary(root)
    failed = False
    for arg in argv:
        resolved = Path(arg).resolve()
        if not resolved.is_file() or not resolved.is_relative_to(root):
            print(f"{arg}: not a file inside the repository", file=sys.stderr)
            failed = True
            continue
        path = resolved.relative_to(root).as_posix()
        for problem in check_page(resolved.read_text(encoding="utf-8"), path=path):
            print(problem)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
