from __future__ import annotations

from lgtmaybe.github import RestGitHubGateway


def _gateway() -> RestGitHubGateway:
    return RestGitHubGateway(repo="owner/repo", pr_number=1, token="test")


def _node(thread_id: str, *, resolved: bool = False, ours: bool = True) -> dict:
    markers = "<!-- lgtmaybe-finding:abc123 -->\n<!-- lgtmaybe-identity:def456 -->" if ours else ""
    return {
        "id": thread_id,
        "isResolved": resolved,
        "isOutdated": True,
        "path": "a.py",
        "comments": {
            "nodes": [
                {
                    "databaseId": 7,
                    "body": f"**[MEDIUM] Bug**\n\nIt fails.\n\n{markers}",
                }
            ]
        },
    }


def test_list_active_findings_returns_only_our_unresolved_roots(monkeypatch) -> None:
    gateway = _gateway()
    monkeypatch.setattr(
        gateway,
        "_walk_review_threads",
        lambda fields: iter(
            [_node("ACTIVE"), _node("DONE", resolved=True), _node("HUMAN", ours=False)]
        ),
    )

    findings = gateway.list_active_findings()

    assert [(f.thread_id, f.path, f.fingerprint, f.identity) for f in findings] == [
        ("ACTIVE", "a.py", "abc123", "def456")
    ]


def test_only_explicitly_fixed_thread_ids_are_resolved(monkeypatch) -> None:
    gateway = _gateway()
    resolved: list[str] = []
    replied: list[str] = []
    monkeypatch.setattr(
        gateway, "_walk_review_threads", lambda fields: iter([_node("FIXED"), _node("UNCERTAIN")])
    )
    monkeypatch.setattr(gateway, "_resolve_thread", resolved.append)
    monkeypatch.setattr(gateway, "_mark_comment_resolved", lambda comment_id, body: None)
    monkeypatch.setattr(
        gateway, "reply_in_thread", lambda thread_id, body: replied.append(thread_id)
    )

    gateway.set_validated_fixed_threads({"FIXED"})
    gateway._resolve_fixed_threads([])

    assert resolved == ["FIXED"]
    assert replied == ["FIXED"]


def test_validated_resolution_reuses_the_active_finding_read(monkeypatch) -> None:
    gateway = _gateway()
    walks = 0

    def walk(fields: str):
        nonlocal walks
        walks += 1
        return iter([_node("FIXED"), _node("UNCERTAIN")])

    monkeypatch.setattr(gateway, "_walk_review_threads", walk)
    monkeypatch.setattr(gateway, "_resolve_thread", lambda thread_id: None)
    monkeypatch.setattr(gateway, "_mark_comment_resolved", lambda comment_id, body: None)
    monkeypatch.setattr(gateway, "reply_in_thread", lambda thread_id, body: None)

    gateway.list_active_findings()
    gateway.set_validated_fixed_threads({"FIXED"})
    gateway._resolve_fixed_threads([])

    assert walks == 1
