"""👎 feedback learning on GitLab — award emoji on our finding discussions.

Mirrors the GitHub reaction tests: a ``thumbsdown`` award on the first note of
one of our finding discussions, left by a member who can push (Developer or
above), suppresses that finding's fingerprint on the next run. On a public
project anyone can award an emoji, so an unprivileged or unverifiable 👎 never
counts.
"""

from __future__ import annotations

from typing import Any

import httpx
import respx

from lgtmaybe.gitlab import GitLabGateway

HOST = "gitlab.example.com"
PROJECT = "group%2Fsub%2Fproject"
API = f"https://{HOST}/api/v4/projects/{PROJECT}"
MR_URL = f"{API}/merge_requests/7"

FP_A = "a" * 12
FP_B = "b" * 12
DEVELOPER = 30
REPORTER = 20


def _gateway() -> GitLabGateway:
    return GitLabGateway(
        host=HOST, repo="group/sub/project", pr_number=7, token="glpat", client=httpx.Client()
    )


def _discussion(note_id: int, body: str) -> dict[str, Any]:
    note = {"id": note_id, "body": body, "resolved": False, "author": {"id": 99}}
    return {"id": f"d{note_id}", "notes": [note]}


def _ours(note_id: int, fingerprint: str) -> dict[str, Any]:
    return _discussion(note_id, f"finding\n<!-- lgtmaybe-finding:{fingerprint} -->")


def _award(name: str, user_id: int) -> dict[str, Any]:
    return {"name": name, "user": {"id": user_id, "username": f"u{user_id}"}}


def _stub(
    discussions: list[dict[str, Any]],
    awards: dict[int, list[dict[str, Any]] | int],
    members: dict[int, int | None],
) -> dict[str, respx.Route]:
    """Discussions, per-note awards (a list, or an HTTP status), and member levels."""
    respx.route(method="GET", url__startswith=f"{MR_URL}/discussions").mock(
        return_value=httpx.Response(200, json=discussions)
    )
    routes: dict[str, respx.Route] = {}
    for note_id, payload in awards.items():
        response = (
            httpx.Response(payload)
            if isinstance(payload, int)
            else httpx.Response(200, json=payload)
        )
        routes[f"award-{note_id}"] = respx.route(
            method="GET", url__startswith=f"{MR_URL}/notes/{note_id}/award_emoji"
        ).mock(return_value=response)
    for user_id, level in members.items():
        response = (
            httpx.Response(404, json={"message": "404 Not found"})
            if level is None
            else httpx.Response(200, json={"id": user_id, "access_level": level})
        )
        routes[f"member-{user_id}"] = respx.get(f"{API}/members/all/{user_id}").mock(
            return_value=response
        )
    return routes


class TestDownvotedFingerprints:
    @respx.mock
    def test_a_developers_thumbs_down_is_collected(self) -> None:
        _stub([_ours(1, FP_A)], {1: [_award("thumbsdown", 5)]}, {5: DEVELOPER})

        assert _gateway().list_downvoted_fingerprints() == {FP_A}

    @respx.mock
    def test_other_emoji_are_not_a_downvote(self) -> None:
        _stub([_ours(1, FP_A)], {1: [_award("thumbsup", 5), _award("eyes", 5)]}, {5: DEVELOPER})

        assert _gateway().list_downvoted_fingerprints() == set()

    @respx.mock
    def test_our_fingerprint_on_someone_elses_note_is_never_read(self) -> None:
        """Anyone can paste a fingerprint; a developer's 👎 on it must not suppress ours."""
        forged = _ours(1, FP_A)
        forged["notes"][0]["author"] = {"id": 4321}
        routes = _stub([forged], {1: [_award("thumbsdown", 5)]}, {5: DEVELOPER})

        assert _gateway().list_downvoted_fingerprints() == set()
        assert not routes["award-1"].called

    @respx.mock
    def test_a_discussion_that_is_not_ours_is_never_read(self) -> None:
        routes = _stub([_discussion(1, "a human comment")], {1: []}, {})

        assert _gateway().list_downvoted_fingerprints() == set()
        assert not routes["award-1"].called

    @respx.mock
    def test_a_reporters_thumbs_down_does_not_count(self) -> None:
        """Reporters cannot push; on a public project anyone can award an emoji."""
        _stub([_ours(1, FP_A)], {1: [_award("thumbsdown", 5)]}, {5: REPORTER})

        assert _gateway().list_downvoted_fingerprints() == set()

    @respx.mock
    def test_a_non_member_fails_closed(self) -> None:
        _stub([_ours(1, FP_A)], {1: [_award("thumbsdown", 5)]}, {5: None})

        assert _gateway().list_downvoted_fingerprints() == set()

    @respx.mock
    def test_an_award_without_a_user_is_ignored(self) -> None:
        _stub([_ours(1, FP_A)], {1: [{"name": "thumbsdown"}]}, {})

        assert _gateway().list_downvoted_fingerprints() == set()

    @respx.mock
    def test_one_authorised_downvote_among_many_is_enough(self) -> None:
        _stub(
            [_ours(1, FP_A)],
            {1: [_award("thumbsdown", 5), _award("thumbsdown", 6)]},
            {5: REPORTER, 6: DEVELOPER},
        )

        assert _gateway().list_downvoted_fingerprints() == {FP_A}

    @respx.mock
    def test_permission_lookups_are_cached_per_user(self) -> None:
        routes = _stub(
            [_ours(1, FP_A), _ours(2, FP_B)],
            {1: [_award("thumbsdown", 5)], 2: [_award("thumbsdown", 5)]},
            {5: DEVELOPER},
        )

        assert _gateway().list_downvoted_fingerprints() == {FP_A, FP_B}
        assert routes["member-5"].call_count == 1

    @respx.mock
    def test_one_unreadable_note_does_not_hide_the_others(self) -> None:
        _stub(
            [_ours(1, FP_A), _ours(2, FP_B)],
            {1: 500, 2: [_award("thumbsdown", 5)]},
            {5: DEVELOPER},
        )

        assert _gateway().list_downvoted_fingerprints() == {FP_B}

    @respx.mock
    def test_resolved_discussions_still_count(self) -> None:
        """A 👎 is a judgement on the finding, not on whether its thread is open."""
        discussion = _ours(1, FP_A)
        discussion["notes"][0]["resolved"] = True
        _stub([discussion], {1: [_award("thumbsdown", 5)]}, {5: DEVELOPER})

        assert _gateway().list_downvoted_fingerprints() == {FP_A}


class TestCapability:
    def test_gitlab_now_claims_feedback(self) -> None:
        from lgtmaybe.core import ports

        assert isinstance(GitLabGateway.__new__(GitLabGateway), ports.SupportsFeedback)
