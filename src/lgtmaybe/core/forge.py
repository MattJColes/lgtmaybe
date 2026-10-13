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
from urllib.parse import unquote, urlsplit


class Forge(StrEnum):
    """A code host lgtmaybe can read a change from and post a review back to."""

    github = "github"
    gitlab = "gitlab"
    gitea = "gitea"
    azure_devops = "azure-devops"


@dataclass(frozen=True, slots=True)
class PRLocator:
    """Everything needed to address one change request, host included.

    ``repo`` is the forge's project path ("owner/repo", a nested
    "group/subgroup/project" on GitLab, or "organisation/project/repository" on
    Azure DevOps, decoded). ``host`` and ``scheme`` are kept because self-hosted
    GitLab and Gitea are the common case, so the API base cannot be a constant.
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

# Azure DevOps is told apart by its ``/_git/<repo>/pullrequest/<id>`` tail, and
# its organisation lives in the host (``<org>.visualstudio.com``) or the first
# path segment (``dev.azure.com/<org>``). The project segment is optional:
# Azure DevOps drops it from a URL when the repository shares the project's name.
_AZURE_DEVOPS_TAIL = re.compile(
    r"^/(?:(?P<project>[^/]+)/)?_git/(?P<name>[^/]+)/pullrequest/(?P<number>\d+)/?$",
    re.IGNORECASE,
)
_AZURE_DEVOPS_SERVER_TAIL = re.compile(r"/_git/[^/]+/pullrequest/\d+/?$", re.IGNORECASE)
_AZURE_DEVOPS_HOST = "dev.azure.com"
_LEGACY_AZURE_DEVOPS_SUFFIX = ".visualstudio.com"

# Every variable a forge's token may arrive in, most explicit first. Azure
# Pipelines hands a job its own short-lived token as SYSTEM_ACCESSTOKEN, so a
# pipeline needs no stored secret at all; a PAT still wins when one is set.
_TOKEN_ENV_VARS: dict[Forge, tuple[str, ...]] = {
    Forge.github: ("GITHUB_TOKEN",),
    Forge.gitlab: ("GITLAB_TOKEN",),
    Forge.gitea: ("GITEA_TOKEN",),
    Forge.azure_devops: ("AZURE_DEVOPS_TOKEN", "SYSTEM_ACCESSTOKEN"),
}


def _parse_azure_devops(host: str, path: str, scheme: str) -> PRLocator | None:
    """An Azure DevOps Services pull request, or None when the URL is not one.

    Each path segment is percent-decoded so a project named "My Project" reads
    as itself; a segment that decodes to contain ``/``, or to a dot segment, is
    refused, because it would make the organisation/project/repository split
    ambiguous or walk the API path.
    """
    lowered = host.lower()
    if lowered == _AZURE_DEVOPS_HOST:
        org, _, rest = path.lstrip("/").partition("/")
        if not org or org.lower() == "_git":
            return None
        tail = _AZURE_DEVOPS_TAIL.match(f"/{rest}")
    elif lowered.endswith(_LEGACY_AZURE_DEVOPS_SUFFIX):
        org = host[: -len(_LEGACY_AZURE_DEVOPS_SUFFIX)]
        tail = _AZURE_DEVOPS_TAIL.match(path)
    else:
        # The same tail on any other host is an on-premises Azure DevOps Server
        # collection: recognisable, so worth naming rather than "could not parse".
        if _AZURE_DEVOPS_SERVER_TAIL.search(path):
            raise ValueError(
                f"{host} looks like Azure DevOps Server (on-premises), which is not supported "
                "yet. lgtmaybe posts to Azure DevOps Services: "
                "https://dev.azure.com/org/project/_git/repo/pullrequest/42"
            )
        return None
    if tail is None or not org or "." in org:
        return None
    name = unquote(tail["name"])
    project = unquote(tail["project"]) if tail["project"] else name
    segments = (unquote(org), project, name)
    if any("/" in segment or segment in {"", ".", ".."} for segment in segments):
        return None
    return PRLocator(
        forge=Forge.azure_devops,
        host=host,
        repo="/".join(segments),
        number=int(tail["number"]),
        scheme=scheme,
    )


def parse_pr_url(pr_url: str) -> PRLocator:
    """Resolve a pull/merge request URL to the forge and project it names.

    Raises ValueError naming every supported shape when nothing matches — the
    user could be on any of four hosts, so a GitHub-only example would send
    most of them looking for a mistake they did not make.
    """
    parsed = urlsplit(pr_url if "://" in pr_url else f"https://{pr_url}")
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported pull/merge request URL scheme: {parsed.scheme!r}")
    azure = _parse_azure_devops(parsed.netloc, parsed.path, parsed.scheme)
    if azure is not None:
        return azure
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
        "https://gitlab.com/org/repo/-/merge_requests/42, "
        "https://gitea.example.com/org/repo/pulls/42, or "
        "https://dev.azure.com/org/project/_git/repo/pullrequest/42"
    )


def token_env_var(forge: Forge) -> str:
    """The primary environment variable holding ``forge``'s API token."""
    return _TOKEN_ENV_VARS[forge][0]


def token_env_vars(forge: Forge) -> tuple[str, ...]:
    """Every variable ``forge``'s token may arrive in, in the order they are tried."""
    return _TOKEN_ENV_VARS[forge]
