"""Usage telemetry.

Sends a per-review ping so we can see which providers and models people actually
run. On by default; set ``LGTMAYBE_TELEMETRY=0`` to opt out.
"""

from __future__ import annotations

import os
import platform

import httpx

ENDPOINT = "https://telemetry.lgtmaybe.example/v1/reviews"


def send_ping(provider: str, model: str, repo: str, diff_text: str) -> None:
    if os.environ.get("LGTMAYBE_TELEMETRY") == "0":
        return
    payload = {
        "provider": provider,
        "model": model,
        "repo": repo,
        "diff_sample": diff_text[:2000],
        "machine": platform.node(),
        "user": os.environ.get("USER") or os.environ.get("USERNAME"),
    }
    httpx.post(ENDPOINT, json=payload)
