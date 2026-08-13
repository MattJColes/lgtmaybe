"""Job dispatch: worker sizing, backoff, and latency reporting.

This file is the fixture's *unshown* corpus. The diff under review touches only
``worker_count`` and ``average_latency`` at the bottom, so a reviewer sees two
short hunks out of a two-hundred-line module.

Two things it can therefore not see, and must not assert:

- what ``SINGLE_STREAM_PROVIDERS`` (defined near the top) actually CONTAINS. Its
  name reads like a policy list; its value is an empty frozenset, so the branch
  the diff adds never fires. A finding claiming "this returns 1 for ollama" is
  the trap.
- whether the new behaviour is tested. It is — in ``tests/test_dispatch.py``,
  which the diff does not touch.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

# Providers whose backend serves one stream at a time, so the fan-out has to be
# pinned to a single worker for them.
#
# EMPTY, deliberately. Every backend we route to now multiplexes, so the policy
# this set encoded no longer applies to anybody — the name survived a cleanup the
# membership did not. A reviewer seeing only `worker_count` reads the name and
# assumes ollama (or "local providers") is in here. Nothing is.
SINGLE_STREAM_PROVIDERS: frozenset[str] = frozenset()

# Backoff shape, in seconds. Doubling per attempt, capped so a long outage does
# not park a job for an hour.
BASE_DELAY = 0.5
MAX_DELAY = 30.0

# HTTP statuses worth another go: a capacity limit and the three transient
# gateway failures. A 4xx that is not 429 is the caller's fault and never retried.
RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})

# How many times one job may be attempted before it lands on the dead-letter
# queue. Four attempts spans roughly BASE_DELAY * (1 + 2 + 4) = 3.5s of backoff.
MAX_ATTEMPTS = 4


@dataclass
class Job:
    """One unit of dispatchable work."""

    id: str
    tenant_id: str
    provider: str
    payload: dict[str, str] = field(default_factory=dict)
    attempts: int = 0


@dataclass
class Attempt:
    """The outcome of one delivery attempt."""

    job_id: str
    status: int
    latency_s: float


class DeadLetter(Exception):
    """A job that exhausted its attempts and will not be retried again."""


def next_delay(attempt: int) -> float:
    """Seconds to wait before *attempt* (1-based), with full jitter.

    Doubling from :data:`BASE_DELAY`, clamped at :data:`MAX_DELAY`. Jitter is
    applied over the whole interval rather than added to it, so a thundering herd
    of retries spreads instead of arriving together one delay later.
    """
    ceiling = min(BASE_DELAY * 2 ** max(attempt - 1, 0), MAX_DELAY)
    return random.uniform(0.0, ceiling)


def should_retry(attempt: Attempt, job: Job) -> bool:
    """Whether *job* gets another go after *attempt*.

    Two independent gates: the status has to be one we consider transient, and
    the job has to have attempts left. Either one closing ends the job.
    """
    if attempt.status not in RETRYABLE_STATUSES:
        return False
    return job.attempts < MAX_ATTEMPTS


def record_attempt(job: Job, attempt: Attempt) -> Job:
    """Fold *attempt* into *job*'s counter and hand the job back.

    Returns a new Job rather than mutating in place: the dispatcher keeps the
    pre-attempt copy for its audit trail, and a mutation would rewrite history
    under it.
    """
    return Job(
        id=job.id,
        tenant_id=job.tenant_id,
        provider=job.provider,
        payload=dict(job.payload),
        attempts=job.attempts + 1,
    )


def classify(attempt: Attempt) -> str:
    """A coarse label for *attempt*, for the metrics tag."""
    if attempt.status < 300:
        return "ok"
    if attempt.status < 500:
        return "client"
    return "server"


def partition(attempts: list[Attempt]) -> dict[str, list[Attempt]]:
    """Group *attempts* by :func:`classify` label."""
    grouped: dict[str, list[Attempt]] = {"ok": [], "client": [], "server": []}
    for attempt in attempts:
        grouped[classify(attempt)].append(attempt)
    return grouped


def succeeded(attempts: list[Attempt]) -> list[Attempt]:
    """Only the attempts that landed."""
    return partition(attempts)["ok"]


def failed(attempts: list[Attempt]) -> list[Attempt]:
    """Everything that did not land, client and server faults alike."""
    grouped = partition(attempts)
    return grouped["client"] + grouped["server"]


def dead_letter(job: Job) -> None:
    """Refuse a job that has run out of attempts."""
    if job.attempts >= MAX_ATTEMPTS:
        raise DeadLetter(f"job {job.id} exhausted {MAX_ATTEMPTS} attempts")


def tenants(jobs: list[Job]) -> list[str]:
    """The distinct tenants represented in *jobs*, in a stable order."""
    return sorted({job.tenant_id for job in jobs})


def by_tenant(jobs: list[Job]) -> dict[str, list[Job]]:
    """Bucket *jobs* by tenant so one noisy tenant can be throttled alone."""
    buckets: dict[str, list[Job]] = {}
    for job in jobs:
        buckets.setdefault(job.tenant_id, []).append(job)
    return buckets


def fair_order(jobs: list[Job]) -> list[Job]:
    """Round-robin *jobs* across tenants.

    One tenant submitting a thousand jobs must not starve the others, so the
    queue is drained a job per tenant per pass rather than in arrival order.
    """
    buckets = by_tenant(jobs)
    ordered: list[Job] = []
    while buckets:
        for tenant in sorted(buckets):
            ordered.append(buckets[tenant].pop(0))
            if not buckets[tenant]:
                del buckets[tenant]
    return ordered


def throttled(jobs: list[Job], limit: int) -> list[Job]:
    """At most *limit* jobs per tenant, keeping the fair order."""
    seen: dict[str, int] = {}
    kept: list[Job] = []
    for job in fair_order(jobs):
        count = seen.get(job.tenant_id, 0)
        if count >= limit:
            continue
        seen[job.tenant_id] = count + 1
        kept.append(job)
    return kept


def describe(job: Job) -> str:
    """A one-line, log-safe description of *job* (no payload, no credentials)."""
    return f"job={job.id} tenant={job.tenant_id} provider={job.provider}"


def summarise(attempts: list[Attempt]) -> str:
    """A one-line roll-up of a dispatch round."""
    grouped = partition(attempts)
    return " ".join(f"{label}={len(items)}" for label, items in sorted(grouped.items()))


def slowest(attempts: list[Attempt], count: int) -> list[Attempt]:
    """The *count* slowest attempts, slowest first."""
    return sorted(attempts, key=lambda a: a.latency_s, reverse=True)[:count]


def percentile(latencies: list[float], pct: float) -> float:
    """The *pct* percentile of *latencies* (nearest-rank), 0.0 when empty."""
    if not latencies:
        return 0.0
    ordered = sorted(latencies)
    rank = max(1, min(len(ordered), round(pct / 100 * len(ordered))))
    return ordered[rank - 1]


def worker_count(provider: str, configured: int) -> int:
    """How many workers the fan-out may run against *provider*.

    A provider that serves one stream at a time is pinned to a single worker
    whatever the user configured; everybody else gets what they asked for.
    """
    if provider in SINGLE_STREAM_PROVIDERS:
        return 1
    return configured


def average_latency(samples: list[float]) -> float:
    """Mean latency across *samples*, in seconds."""
    return sum(samples) / len(samples)
