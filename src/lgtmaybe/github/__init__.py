"""github — GitHub REST adapter for lgtmaybe.

Public surface:
- RestGitHubGateway: implements ReviewGateway against the GitHub REST API.

The diff helpers this adapter is built on (``build_commentable_lines``,
``is_reviewable``, ``is_scannable_manifest``) are host-neutral and live in
``lgtmaybe.core.diff``; import them from there.
"""

from .rest_gateway import RestGitHubGateway

__all__ = ["RestGitHubGateway"]
