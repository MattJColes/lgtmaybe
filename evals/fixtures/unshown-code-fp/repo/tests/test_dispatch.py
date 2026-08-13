"""Tests for the dispatch module — the file the diff does NOT touch.

This is the whole point of the ``test-exists-elsewhere`` trap. ``worker_count``
arrives in the diff with no test beside it, so a reviewer reading only the diff
can truthfully say "this change adds no test". That claim is nonetheless wrong as
a defect: the coverage is right here, in a file the PR had no reason to change.

Reflection's gap-finding carve-out protects missing-test findings by design, so
this is the shape the auditor is currently instructed NOT to prune — which is
exactly what makes it worth measuring.
"""

from __future__ import annotations

import pytest
from scheduler.dispatch import (
    SINGLE_STREAM_PROVIDERS,
    Attempt,
    Job,
    average_latency,
    should_retry,
    worker_count,
)


def test_worker_count_honours_the_configured_width() -> None:
    assert worker_count("openai", 8) == 8
    assert worker_count("ollama", 8) == 8


def test_worker_count_pins_a_single_stream_provider_to_one_worker() -> None:
    """The branch is real even though nothing currently routes through it."""
    assert worker_count("openai", 8) == 8
    for provider in SINGLE_STREAM_PROVIDERS:
        assert worker_count(provider, 8) == 1


def test_worker_count_passes_a_width_of_one_through() -> None:
    assert worker_count("anthropic", 1) == 1


def test_average_latency_is_the_mean_of_the_samples() -> None:
    assert average_latency([1.0, 2.0, 3.0]) == 2.0


def test_should_retry_needs_both_a_transient_status_and_a_spare_attempt() -> None:
    job = Job(id="j1", tenant_id="t1", provider="openai")
    assert should_retry(Attempt(job_id="j1", status=503, latency_s=0.1), job)
    assert not should_retry(Attempt(job_id="j1", status=404, latency_s=0.1), job)


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_every_retryable_status_is_retried(status: int) -> None:
    job = Job(id="j1", tenant_id="t1", provider="openai")
    assert should_retry(Attempt(job_id="j1", status=status, latency_s=0.1), job)
