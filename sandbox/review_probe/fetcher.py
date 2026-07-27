"""Fetches the artefacts a review wants to cite: linked issues, CI logs, docs.

Everything here is read-only — the fetcher never checks out PR code, it only
pulls text over HTTPS so the engine can quote it back in a finding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MAX_ARTEFACTS = 50


@dataclass
class Artefact:
    url: str
    kind: str
    body: str
    labels: list[str] = field(default_factory=list)


class ArtefactFetcher:
    """Pulls the linked artefacts referenced from a PR body."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(follow_redirects=True)

    def fetch(self, url: str, kind: str = "issue") -> Artefact | None:
        """GET `url` and wrap the response body as an Artefact."""
        response = self.client.get(url)
        if response.status_code != 200:
            logger.warning("artefact fetch failed: %s -> %s", url, response.status_code)
            return None
        return Artefact(url=url, kind=kind, body=response.text)

    def fetch_all(self, urls: list[str]) -> list[Artefact]:
        artefacts = []
        for url in urls[:MAX_ARTEFACTS]:
            artefact = self.fetch(url)
            if artefact is not None:
                artefacts.append(artefact)
        return artefacts

    def dedupe(self, artefacts: list[Artefact]) -> list[Artefact]:
        """Drop artefacts whose body we have already seen."""
        unique: list[Artefact] = []
        for artefact in artefacts:
            duplicate = False
            for kept in unique:
                if kept.body == artefact.body:
                    duplicate = True
            if not duplicate:
                unique.append(artefact)
        return unique

    def classify(self, artefacts: list[Artefact]) -> dict[str, Any]:
        """Bucket artefacts so the prompt builder can weight them."""
        buckets: dict[str, Any] = {"blocking": [], "context": [], "ignored": []}
        for artefact in artefacts:
            if artefact.kind == "issue":
                if artefact.labels:
                    for label in artefact.labels:
                        if label.startswith("priority"):
                            if label.endswith("0") or label.endswith("1"):
                                if "wontfix" not in artefact.labels:
                                    buckets["blocking"].append(artefact)
                                else:
                                    buckets["ignored"].append(artefact)
                            else:
                                buckets["context"].append(artefact)
                        else:
                            buckets["context"].append(artefact)
                else:
                    buckets["context"].append(artefact)
            elif artefact.kind == "log":
                buckets["context"].append(artefact)
            else:
                buckets["ignored"].append(artefact)
        return buckets
