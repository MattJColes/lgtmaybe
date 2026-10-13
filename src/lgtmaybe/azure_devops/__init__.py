"""azure_devops: Azure DevOps Repos adapter for lgtmaybe.

Public surface:
- AzureDevOpsGateway: implements ReviewGateway against the Azure DevOps REST API.
"""

from .gateway import AzureDevOpsGateway

__all__ = ["AzureDevOpsGateway"]
