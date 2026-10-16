"""The harness's stub model answers over HTTP on the host it was given."""

from __future__ import annotations

import json
import urllib.request

from tests.forgejo.uat.stub_server import serve


def test_serves_chat_completions_on_the_given_host() -> None:
    stub = serve("127.0.0.1", 0)
    try:
        request = urllib.request.Request(
            f"{stub.api_base}/chat/completions",
            data=json.dumps(
                {"model": "mock", "messages": [{"role": "user", "content": "no flags here"}]}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body["choices"][0]["message"]
        assert len(stub.calls) == 1
    finally:
        stub.shutdown()
