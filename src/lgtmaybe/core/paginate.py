"""Page-walk pagination, shared by the GitLab and Gitea adapters.

A short page means different things on the two forges. Gitea lets an admin cap
every page below the requested size (``[api] MAX_RESPONSE_ITEMS``), so there a
page shorter than ``limit`` proves nothing — but Gitea reports the listing's
size in ``X-Total-Count``, and the walk runs until it has that many. GitLab
sends no such header and honours the requested ``per_page`` up to its fixed
maximum of 100, so there the first page shorter than requested is the last one.
The walk trusts ``X-Total-Count`` when present and falls back to the short-page
rule when not, so neither forge pays an extra request on a complete listing.
They differ otherwise only in what the page-size parameter is called
(``per_page`` vs ``limit``), so the walk is host-neutral and lives here.

An empty page always ends the walk, so an over-counting total can't loop.
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
    """Every page of a forge list endpoint, flattened.

    Args:
        client:     the adapter's HTTP client.
        url:        the list endpoint.
        headers:    auth headers, sent on every page.
        page_param: what this forge calls the page-size parameter
                    (``per_page`` on GitLab, ``limit`` on Gitea).
        limit:      the page size to request; with no ``X-Total-Count``
                    header, a shorter page ends the walk.
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
        total = resp.headers.get("X-Total-Count", "")
        done = len(items) >= int(total) if total.isdigit() else len(batch) < limit
        if done:
            return items
        page += 1
