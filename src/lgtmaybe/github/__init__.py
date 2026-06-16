"""github — GitHub REST adapter for lgtmaybe.

Public surface:
- RestGitHubGateway: implements GitHubGateway against the GitHub REST API.
- build_commentable_lines: parse a unified diff into the set of (file, line, side)
  tuples a review comment can anchor to.
- is_reviewable: predicate that rejects lockfiles, minified, vendored, binary paths.
"""

from .diff import build_commentable_lines, is_reviewable
from .rest_gateway import RestGitHubGateway

__all__ = ["RestGitHubGateway", "build_commentable_lines", "is_reviewable"]
