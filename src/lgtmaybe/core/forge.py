"""Which code host a review targets, and how to authenticate to it.

lgtmaybe reviews merge requests on more than one forge. Almost nothing in the
codebase needs to know which one: the engine never sees a gateway at all, and
the diff, prompt, and finding types are host-neutral by construction. The
knowledge is concentrated here — parse a URL into a locator, name the token
variable — so a new forge is an adapter plus an entry in these tables, not a
change spread through the pipeline.

"Forge" rather than "provider" deliberately: ``Provider`` is already taken by
the LLM backend, which is a separate axis (any forge can be reviewed by any
model).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class Forge(StrEnum):
    """A code host lgtmaybe can read a change from and post a review back to."""

    github = "github"
    gitlab = "gitlab"
    gitea = "gitea"


@dataclass(frozen=True, slots=True)
class PRLocator:
    """Everything needed to address one change request, host included.

    ``repo`` is the forge's project path ("owner/repo", or a nested
    "group/subgroup/project" on GitLab). ``host`` and ``scheme`` are kept because
    self-hosted GitLab and Gitea are the common case, so the API base cannot be
    a constant.
    """

    forge: Forge
    host: str
    repo: str
    number: int
    scheme: str = "https"


# One pattern per forge, discriminated by the segment before the number. GitHub
# and Gitea differ only in singular/plural ("pull" vs "pulls"), so Gitea is
# matched first — its pattern is the more specific of the two. GitLab's ``/-/``
# separator is what makes an arbitrarily nested group path unambiguous.
_URL_PATTERNS: tuple[tuple[Forge, re.Pattern[str]], ...] = (
    (
        Forge.gitlab,
        re.compile(r"(?P<host>[^/]+)/(?P<repo>.+?)/-/merge_requests/(?P<number>\d+)"),
    ),
    (
        Forge.gitea,
        re.compile(r"(?P<host>[^/]+)/(?P<repo>[^/]+/[^/]+)/pulls/(?P<number>\d+)"),
    ),
    (
        Forge.github,
        re.compile(r"(?P<host>[^/]+)/(?P<repo>[^/]+/[^/]+)/pull/(?P<number>\d+)"),
    ),
)

_TOKEN_ENV_VARS: dict[Forge, str] = {
    Forge.github: "GITHUB_TOKEN",
    Forge.gitlab: "GITLAB_TOKEN",
    Forge.gitea: "GITEA_TOKEN",
}


def parse_pr_url(pr_url: str) -> PRLocator:
    """Resolve a pull/merge request URL to the forge and project it names.

    Raises ValueError naming every supported shape when nothing matches — the
    user could be on any of three hosts, so a GitHub-only example would send
    two thirds of them looking for a mistake they did not make.
    """
    parsed = urlsplit(pr_url if "://" in pr_url else f"https://{pr_url}")
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported pull/merge request URL scheme: {parsed.scheme!r}")
    stripped = f"{parsed.netloc}{parsed.path}"
    for forge, pattern in _URL_PATTERNS:
        match = pattern.search(stripped)
        if match is not None:
            return PRLocator(
                forge=forge,
                host=match["host"],
                repo=match["repo"],
                number=int(match["number"]),
                scheme=parsed.scheme,
            )
    raise ValueError(
        f"Could not parse a pull/merge request URL from {pr_url!r}. Expected one of "
        "https://github.com/org/repo/pull/42, "
        "https://gitlab.com/org/repo/-/merge_requests/42, or "
        "https://gitea.example.com/org/repo/pulls/42"
    )


def token_env_var(forge: Forge) -> str:
    """The environment variable holding ``forge``'s API token."""
    return _TOKEN_ENV_VARS[forge]
