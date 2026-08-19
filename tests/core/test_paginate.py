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
