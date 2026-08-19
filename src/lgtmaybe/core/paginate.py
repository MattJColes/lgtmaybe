"""Short-page pagination, shared by the GitLab and Gitea adapters.

Both forges signal the end of a listing the same way — the first page that
comes back shorter than the requested size is the last one — and neither sends
GitHub's ``Link`` header. They differ only in what the page-size parameter is
called (``per_page`` vs ``limit``) and how large a page may be, so the walk
itself is host-neutral and lives here rather than in either adapter.

Stopping on a short page (rather than reading a next-page header) also keeps a
response without pagination headers — and a mocked one — behaving the same.
"""

from __future__ import annotations

from typing import Any

import httpx

_TIMEOUT = 30.0


def paginate_pages(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    *,
    page_param: str,
    limit: int,
    timeout: float = _TIMEOUT,
) -> list[dict[str, Any]]:
    """Every page of a short-page list endpoint, flattened.

    Args:
        client:     the adapter's HTTP client.
        url:        the list endpoint.
        headers:    auth headers, sent on every page.
        page_param: what this forge calls the page-size parameter
                    (``per_page`` on GitLab, ``limit`` on Gitea).
        limit:      the page size to request; a shorter page ends the walk.
        timeout:    per-request timeout.

    A payload that isn't a list — an error object, an unexpected shape — ends
    the walk with what has been collected so far rather than raising, matching
    the defensive posture the adapters already take with forge payloads. An
    HTTP error status still raises, so a failed listing is never silently
    reported as a short one.
    """
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        resp = client.get(
            url,
            headers=headers,
            params={"page": page, page_param: limit},
            timeout=timeout,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            return items
        items.extend(batch)
        if len(batch) < limit:
            return items
        page += 1
