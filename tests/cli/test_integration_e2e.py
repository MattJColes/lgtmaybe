"""End-to-end integration: real RestGitHubGateway + real LLMReviewEngine.

The provider is the only fake — every other stage (fetch → engine → post) runs
the real code against a respx-mocked GitHub API. This is the integration-step
acceptance test: an e2e run produces inline comments at the correct diff
positions plus a summary.
"""

from __future__ import annotations

import json

import httpx
import respx

from lgtmaybe.cli import run_review
from lgtmaybe.core.models import (
    Provider,
    ProviderResult,
    ReviewConfig,
    ReviewFinding,
    Severity,
)
from lgtmaybe.core.ports import Message
from lgtmaybe.engine import LLMReviewEngine
from lgtmaybe.github import RestGitHubGateway
from tests.fakes import FakeProvider

REPO = "owner/repo"
PR_NUMBER = 42
TOKEN = "ghp_test"

BASE_URL = "https://api.github.com"
PR_URL = f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}"
FILES_URL = f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/files"
REVIEWS_URL = f"{PR_URL}/reviews"

# src/app.py new-file line 2 ("+import sys") is an added line (RIGHT side).
SAMPLE_DIFF = """\
diff --git a/src/app.py b/src/app.py
index 0000001..0000002 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,4 +1,6 @@
 import os
+import sys

 def main():
-    pass
+    print("hello")
+    return 0
"""

_FINDING = ReviewFinding(
    path="src/app.py",
    line=2,
    severity=Severity.medium,
    title="Import order",
    body="sys should be sorted before os",
    failure_scenario=(
        "When the module is linted, the changed import order fails the configured check."
    ),
)


class _ReviewThenReflectProvider(FakeProvider):
    """Answers every lens with the same finding, then a keep-all reflection verdict.

    Which call it is comes from the prompt, not a counter: a counter answered
    lens 1 and fed the reflection verdict to lenses 2..N, so those calls parsed
    as failures and the "review" under test was a partially-failed one carrying
    an incomplete-results notice. Every lens answering makes this a complete
    review — dedupe collapses the identical findings to the one comment asserted
    below — and leaves the partial-failure path to
    tests/test_incomplete_visibility.py.
    """

    def complete(self, messages: list[Message], model: str, **opts: object) -> ProviderResult:
        self.calls.append({"messages": messages, "model": model, "opts": opts})
        prompt = "\n".join(str(m.get("content", "")) for m in messages)
        if "auditing another reviewer" in prompt:
            verdict = json.dumps({0: True})
            return ProviderResult(text=verdict, input_tokens=5, output_tokens=5)
        text = json.dumps([_FINDING.model_dump(mode="json")])
        return ProviderResult(text=text, input_tokens=10, output_tokens=20)


def _mock_github(captured: list[dict[object, object]]) -> None:
    pr_detail = {"number": PR_NUMBER, "base": {"sha": "abc"}, "head": {"sha": "def"}}
    files = [{"filename": "src/app.py"}]

    respx.route(
        method="GET", url=PR_URL, headers={"Accept": "application/vnd.github.v3.diff"}
    ).mock(return_value=httpx.Response(200, content=SAMPLE_DIFF.encode()))
    respx.route(method="GET", url=PR_URL).mock(return_value=httpx.Response(200, json=pr_detail))
    respx.route(method="GET", url__startswith=FILES_URL).mock(
        return_value=httpx.Response(200, json=files)
    )
    respx.route(method="GET", url__startswith=f"{PR_URL}/commits").mock(
        return_value=httpx.Response(200, json=[])
    )
    # Head content for surrounding-context expansion (fetched per reviewable file).
    respx.route(method="GET", url__startswith=f"{BASE_URL}/repos/{REPO}/contents/").mock(
        return_value=httpx.Response(
            200, text="import os\nimport sys\n\ndef main():\n    print('hello')\n    return 0\n"
        )
    )
    respx.route(method="GET", url=REVIEWS_URL).mock(return_value=httpx.Response(200, json=[]))

    def capture_create(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 1})

    respx.route(method="POST", url=REVIEWS_URL).mock(side_effect=capture_create)


@respx.mock
def test_e2e_posts_inline_comment_at_correct_position_and_summary() -> None:
    captured: list[dict[object, object]] = []
    _mock_github(captured)

    github = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN)
    engine = LLMReviewEngine(_ReviewThenReflectProvider())
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")

    findings, summary = run_review(github=github, engine=engine, cfg=cfg, dry_run=False)

    assert len(captured) == 1
    body = captured[0]

    comments = body["comments"]
    assert len(comments) == 1
    assert comments[0]["path"] == "src/app.py"
    assert comments[0]["line"] == 2  # anchored by new-file line + side
    assert comments[0]["side"] == "RIGHT"

    # The review body carries the summary + the idempotency marker.
    assert "<!-- lgtmaybe -->" in str(body["body"])
    assert summary in str(body["body"])
    assert "llama3" in summary
    assert "cost" not in summary.lower()


@respx.mock
def test_e2e_dry_run_posts_nothing() -> None:
    captured: list[dict[object, object]] = []
    _mock_github(captured)

    github = RestGitHubGateway(repo=REPO, pr_number=PR_NUMBER, token=TOKEN)
    engine = LLMReviewEngine(_ReviewThenReflectProvider())
    cfg = ReviewConfig(provider=Provider.ollama, model="llama3")

    findings, summary = run_review(github=github, engine=engine, cfg=cfg, dry_run=True)

    assert captured == []
    assert len(findings) == 1
