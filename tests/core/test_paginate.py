"""The shared short-page paginator used by the GitLab and Gitea adapters."""

from __future__ import annotations

from typing import Any

import httpx

from lgtmaybe.core.paginate import paginate_pages


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_single_short_page_is_returned_without_a_second_request() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=[{"id": 1}, {"id": 2}])

    items = paginate_pages(
        _client(handler), "https://x/api/things", {}, page_param="per_page", limit=50
    )

    assert items == [{"id": 1}, {"id": 2}]
    assert seen == [{"page": "1", "per_page": "50"}]


def test_full_pages_are_followed_until_a_short_one_ends_the_walk() -> None:
    pages = {1: [{"id": i} for i in range(3)], 2: [{"id": 3}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[int(request.url.params["page"])])

    items = paginate_pages(
        _client(handler), "https://x/api/things", {}, page_param="limit", limit=3
    )

    assert [item["id"] for item in items] == [0, 1, 2, 3]


def test_the_page_size_parameter_is_named_by_the_caller() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=[])

    paginate_pages(_client(handler), "https://x/api/things", {}, page_param="limit", limit=50)

    assert seen == [{"page": "1", "limit": "50"}]


def test_a_non_list_payload_ends_the_walk_instead_of_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": "not a list"})

    assert (
        paginate_pages(
            _client(handler), "https://x/api/things", {}, page_param="per_page", limit=50
        )
        == []
    )


def test_headers_ride_along_on_every_page_request() -> None:
    seen: list[str | None] = []
    pages = {1: [{"id": 0}], 2: []}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json=pages[int(request.url.params["page"])])

    paginate_pages(
        _client(handler),
        "https://x/api/things",
        {"Authorization": "Bearer t"},
        page_param="per_page",
        limit=1,
    )

    assert seen == ["Bearer t", "Bearer t"]


def test_an_http_error_propagates_rather_than_silently_truncating() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    try:
        paginate_pages(
            _client(handler), "https://x/api/things", {}, page_param="per_page", limit=50
        )
    except httpx.HTTPStatusError:
        return
    raise AssertionError("expected the 500 to propagate")


def _capped_server(total: int, cap: int, seen: list[int]) -> Any:
    """A forge whose admin caps every page at ``cap`` items, whatever was asked."""

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        seen.append(page)
        start = (page - 1) * cap
        rows = [{"id": i} for i in range(start, min(start + cap, total))]
        return httpx.Response(200, json=rows, headers={"X-Total-Count": str(total)})

    return handler


def test_a_server_capping_pages_below_the_limit_is_walked_to_its_total_count() -> None:
    # Gitea's [api] MAX_RESPONSE_ITEMS = 30 makes every page "short" of limit=50.
    seen: list[int] = []

    items = paginate_pages(
        _client(_capped_server(80, 30, seen)),
        "https://x/api/files",
        {},
        page_param="limit",
        limit=50,
    )

    assert [item["id"] for item in items] == list(range(80))
    assert seen == [1, 2, 3]


def test_reaching_the_total_count_ends_the_walk_without_an_empty_page() -> None:
    seen: list[int] = []

    items = paginate_pages(
        _client(_capped_server(100, 50, seen)),
        "https://x/api/files",
        {},
        page_param="limit",
        limit=50,
    )

    assert len(items) == 100
    assert seen == [1, 2], "the total says the listing is complete; no third request"


def test_an_empty_page_ends_the_walk_even_short_of_the_total_count() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        rows = [{"id": 0}] if request.url.params["page"] == "1" else []
        return httpx.Response(200, json=rows, headers={"X-Total-Count": "5"})

    items = paginate_pages(_client(handler), "https://x/api/x", {}, page_param="limit", limit=1)

    assert items == [{"id": 0}]
