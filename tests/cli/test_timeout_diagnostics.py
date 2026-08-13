"""The effective per-call timeout is announced, with where it came from.

A timed-out review reports the budget it blew ("provider request exceeded 60s")
but never where that budget came from — so an explicit `timeout: 60` in a repo's
`.lgtmaybe.yml` and a 60s built-in default produce identical evidence. That
ambiguity cost a real investigation, so the resolved value and its source are
logged before the first call.
"""

from __future__ import annotations

import importlib.metadata
import logging
from typing import NoReturn

import pytest

from lgtmaybe.cli import build_provider_engine
from lgtmaybe.core.models import Provider, ReviewConfig
from lgtmaybe.engine.engine import concurrency_cap
from lgtmaybe.providers.factory import default_timeout_for


def _raise(exc: Exception) -> NoReturn:
    raise exc


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def cli_logs():
    """Capture the CLI module's INFO logs (its logger does not propagate)."""
    import lgtmaybe.cli as cli_module

    handler = _ListHandler()
    logger = cli_module._log
    prev = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev)


def _runtime(**overrides):
    from lgtmaybe.cli import RuntimeOptions

    return RuntimeOptions(api_key="sk-test", **overrides)


def _timeout_record(records: list[logging.LogRecord]) -> logging.LogRecord:
    matching = [r for r in records if "timeout" in r.getMessage()]
    assert matching, "expected the resolved per-call timeout to be logged"
    return matching[0]


def test_explicit_timeout_is_logged_and_named_as_configured(cli_logs) -> None:
    cfg = ReviewConfig(provider=Provider.openrouter, model="m", timeout=60)
    build_provider_engine(cfg, _runtime())

    record = _timeout_record(cli_logs)
    assert getattr(record, "timeout_s", None) == 60
    assert getattr(record, "timeout_source", None) == "configured"


def test_auto_timeout_is_logged_and_named_as_a_default(cli_logs) -> None:
    cfg = ReviewConfig(provider=Provider.openrouter, model="m")
    build_provider_engine(cfg, _runtime())

    record = _timeout_record(cli_logs)
    assert getattr(record, "timeout_s", None) == default_timeout_for(Provider.openrouter)
    assert getattr(record, "timeout_source", None) == "provider default"


def test_the_running_build_is_named_alongside_the_budget(cli_logs, monkeypatch) -> None:
    """The Action pins a floating image tag, so a budget that looks impossible
    against the documented default is usually an older build. The run has to say
    which build it is, or the evidence can't be read at all.

    The version is stubbed rather than read from the environment: what matters is
    that the resolved value reaches the log record, not that this test host happens
    to have distribution metadata installed.
    """
    import lgtmaybe.core.version as version_module

    monkeypatch.setattr(version_module.metadata, "version", lambda _name: "1.2.3")
    cfg = ReviewConfig(provider=Provider.openrouter, model="m")
    build_provider_engine(cfg, _runtime())

    assert getattr(_timeout_record(cli_logs), "lgtmaybe_version", None) == "1.2.3"


@pytest.mark.parametrize(
    "failure",
    [
        # The expected case: a source checkout that was never installed.
        lambda: _raise(importlib.metadata.PackageNotFoundError("lgtmaybe")),
        # And anything else the metadata read can throw — an unreadable or
        # half-written dist-info surfaces as an OSError, and a broken version
        # lookup must never be the reason a review fails.
        lambda: _raise(OSError("dist-info is unreadable")),
    ],
    ids=["not-installed", "unreadable-metadata"],
)
def test_an_unreadable_version_does_not_break_the_run(monkeypatch, failure) -> None:
    import lgtmaybe.core.version as version_module

    monkeypatch.setattr(version_module.metadata, "version", lambda _name: failure())
    assert version_module.package_version() == "unknown"


def test_the_two_sources_are_distinguishable_at_the_same_value(cli_logs) -> None:
    """The whole point: a configured 600 and a default 600 must not look alike."""
    cloud_default = default_timeout_for(Provider.openai)
    cfg = ReviewConfig(provider=Provider.openai, model="m", timeout=cloud_default)
    build_provider_engine(cfg, _runtime())

    record = _timeout_record(cli_logs)
    assert getattr(record, "timeout_s", None) == cloud_default
    assert getattr(record, "timeout_source", None) == "configured"


def test_a_local_default_is_widened_for_the_fan_out_it_will_queue_behind(cli_logs) -> None:
    """The CLI is the only place that knows both numbers — the provider and the
    fan-out width — so it is where the scaling has to be applied. Getting this
    wiring wrong is invisible: the unscaled budget still works on a fast box and
    only fails on the slow one the scaling exists for.
    """
    cfg = ReviewConfig(provider=Provider.ollama, model="m")
    provider = build_provider_engine(cfg, _runtime())[1]

    unscaled = default_timeout_for(Provider.ollama)
    expected = min(unscaled * concurrency_cap(cfg), cfg.max_review_seconds)
    assert expected > unscaled, "the fixture must actually exercise a widening"
    assert provider.default_opts["timeout"] == expected

    record = _timeout_record(cli_logs)
    assert getattr(record, "timeout_s", None) == expected
    assert getattr(record, "concurrency", None) == concurrency_cap(cfg)


def test_a_serial_local_run_gets_the_unscaled_budget(cli_logs) -> None:
    """Width 1 has no queue, so the mitigation must cost it nothing."""
    cfg = ReviewConfig(provider=Provider.ollama, model="m", max_concurrency=1)
    provider = build_provider_engine(cfg, _runtime())[1]

    assert provider.default_opts["timeout"] == default_timeout_for(Provider.ollama)


def test_a_hosted_default_is_not_widened_by_the_fan_out(cli_logs) -> None:
    cfg = ReviewConfig(provider=Provider.openai, model="m")
    provider = build_provider_engine(cfg, _runtime())[1]

    assert provider.default_opts["timeout"] == default_timeout_for(Provider.openai)


def test_the_widened_budget_never_outlives_the_whole_review_deadline(cli_logs) -> None:
    """Scaling by width is a mitigation, not a licence to run forever.

    Six workers on the generous local default is 3 hours of per-call budget, and
    the fan-out all starts at once — so the whole-review deadline, which only
    skips calls that have not *started*, cannot cut it short. Clamping the scaled
    value to that deadline restores the property the deadline exists for: no
    single call outlives the run it belongs to.
    """
    cfg = ReviewConfig(provider=Provider.ollama, model="m")
    provider = build_provider_engine(cfg, _runtime())[1]

    resolved = provider.default_opts["timeout"]
    assert resolved <= cfg.max_review_seconds
    assert resolved > default_timeout_for(Provider.ollama), "still widened, just bounded"


def test_a_disabled_deadline_leaves_the_scaling_unbounded(cli_logs) -> None:
    """`max_review_seconds: 0` means "no deadline" — it must not read as a
    zero-second ceiling that collapses every budget to nothing."""
    cfg = ReviewConfig(provider=Provider.ollama, model="m", max_review_seconds=0)
    provider = build_provider_engine(cfg, _runtime())[1]

    assert provider.default_opts["timeout"] == default_timeout_for(
        Provider.ollama, concurrency=concurrency_cap(cfg)
    )


def test_a_short_deadline_never_cuts_below_the_providers_own_default(cli_logs) -> None:
    """The clamp may only take back what the scaling added. A tight deadline
    must not drag a local call below the budget it would have had unscaled —
    that would be a regression dressed up as a safety bound."""
    cfg = ReviewConfig(provider=Provider.ollama, model="m", max_review_seconds=60)
    provider = build_provider_engine(cfg, _runtime())[1]

    assert provider.default_opts["timeout"] == default_timeout_for(Provider.ollama)
