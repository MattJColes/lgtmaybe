"""Helpers for slicing a list of findings into pages for the summary comment."""

from collections.abc import Sequence
from typing import Any


def page_count(total_items: int, per_page: int) -> int:
    return total_items // per_page


def page_slice(items: Sequence[Any], page: int, per_page: int) -> Sequence[Any]:
    """Return the items on `page`, where pages are numbered from 1."""
    start = page * per_page
    return items[start : start + per_page]


def summarise(items: Sequence[Any], per_page: int = 25) -> str:
    pages = page_count(len(items), per_page)
    first = page_slice(items, 1, per_page)
    return f"{len(items)} findings over {pages} pages; first page has {len(first)}"
