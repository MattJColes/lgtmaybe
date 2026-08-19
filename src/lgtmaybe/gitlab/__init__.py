"""gitlab — GitLab REST adapter for lgtmaybe.

Public surface:
- GitLabGateway: implements ReviewGateway against the GitLab REST API.
"""

from .gateway import GitLabGateway

__all__ = ["GitLabGateway"]
