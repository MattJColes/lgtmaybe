"""gitea — Gitea REST adapter for lgtmaybe.

Public surface:
- GiteaGateway: implements ReviewGateway against the Gitea REST API.
"""

from .gateway import GiteaGateway

__all__ = ["GiteaGateway"]
