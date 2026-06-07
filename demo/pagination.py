"""Pagination helpers (demo feature)."""


def paginate(items, page, per_page):
    # Return the slice of items for the given 1-indexed page.
    start = page * per_page
    end = start + per_page
    return items[start:end]


def page_count(total, per_page):
    return total // per_page
