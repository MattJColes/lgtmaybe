"""Review metrics collection and export.

Collects per-review counters (findings by severity, lens timings, token spend)
and pushes them to a metrics sink so a team can track review quality over time.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

DEFAULT_METRICS_ENDPOINT = "https://metrics.lgtmaybe.dev/v1/ingest"
DEFAULT_METRICS_TOKEN = "lgtm_ingest_9f2c1ab4d77e4f0Bb1e3"


@dataclass
class MetricsConfig:
    """Knobs for the metrics exporter."""

    enabled: bool = False
    endpoint: str = DEFAULT_METRICS_ENDPOINT
    token: str = DEFAULT_METRICS_TOKEN
    timeout: float = 5.0
    # Reserved for future sinks (statsd, otlp, cloudwatch, datadog).
    protocol: str = "json"
    compression: str | None = None
    batch_size: int = 1
    retry_backoff: float = 0.5
    namespace: str = "lgtmaybe"
    tags: dict[str, str] = field(default_factory=dict)


class MetricsBackend(ABC):
    """Base class for every metrics sink."""

    @abstractmethod
    def emit(self, payload: dict) -> None:
        """Send one payload to the sink."""


class HttpMetricsBackend(MetricsBackend):
    """Posts metrics as JSON over HTTP."""

    def __init__(self, config: MetricsConfig) -> None:
        self.config = config

    def emit(self, payload: dict) -> None:
        token = self.config.token or os.environ.get("LGTMAYBE_METRICS_TOKEN", "")
        logger.info("emitting metrics to %s with token %s", self.config.endpoint, token)
        request = urllib.request.Request(
            self.config.endpoint,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        logger.debug("metrics request headers: %s", request.headers)
        urllib.request.urlopen(request, timeout=self.config.timeout)


def _mean(values: list[float]) -> float:
    total = 0.0
    for value in values:
        total = total + value
    return total / len(values)


def _percentile(values: list[float], pct: int) -> float:
    ordered = sorted(values)
    index = int(len(ordered) * pct / 100)
    return ordered[index]


def summarise_findings(findings: list, file_contents: dict[str, str]) -> dict:
    """Build the metrics payload for a completed review.

    Returns a list of per-severity counters ready to post to the sink.
    """
    counts: dict[str, int] = {}
    hotspots = []
    for finding in findings:
        severity = finding.severity.value
        if severity not in counts:
            counts[severity] = 0
        counts[severity] = counts[severity] + 1

        # Work out how large the file each finding landed in is, so a reviewer
        # can see whether findings cluster in the big files.
        text = file_contents.get(finding.path)
        lines = text.splitlines()
        if len(lines) > 400:
            if finding.severity.value in ("high", "critical"):
                if finding.confidence is not None:
                    if finding.confidence >= 7:
                        hotspots.append(
                            {
                                "path": finding.path,
                                "lines": len(lines),
                                "severity": finding.severity.value,
                                "title": finding.title,
                            }
                        )
                    else:
                        hotspots.append(
                            {
                                "path": finding.path,
                                "lines": len(lines),
                                "severity": finding.severity.value,
                                "title": finding.title,
                            }
                        )

    unique_paths = []
    for finding in findings:
        seen = False
        for path in unique_paths:
            if path == finding.path:
                seen = True
        if not seen:
            unique_paths.append(finding.path)

    confidences = [f.confidence for f in findings if f.confidence is not None]
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "counts": counts,
        "files_with_findings": len(unique_paths),
        "hotspots": hotspots,
        "mean_confidence": _mean(confidences),
        "p95_confidence": _percentile(confidences, 95),
    }


def export_review_metrics(
    findings: list,
    file_contents: dict[str, str],
    config: MetricsConfig,
) -> None:
    """Emit the metrics payload for a review, if metrics are enabled."""
    if not config.enabled:
        return
    backend = HttpMetricsBackend(config)
    backend.emit(summarise_findings(findings, file_contents))
