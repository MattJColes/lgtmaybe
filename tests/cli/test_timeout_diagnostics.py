"""The effective per-call timeout is announced, with where it came from.

A timed-out review reports the budget it blew ("provider request exceeded 60s")
but never where that budget came from — so an explicit `timeout: 60` in a repo's
`.lgtmaybe.yml` and a 60s built-in default produce identical evidence. That
ambiguity cost a real investigation, so the resolved value and its source are
logged before the first call.
"""

from __future__ import annotations

import logging

import pytest

from lgtmaybe.cli import build_provider_engine
from lgtmaybe.core.models import Provider, ReviewConfig
from lgtmaybe.providers.factory import default_timeout_for


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
    import lgtmaybe.cli as cli_module

    monkeypatch.setattr(cli_module.metadata, "version", lambda _name: "1.2.3")
    cfg = ReviewConfig(provider=Provider.openrouter, model="m")
    build_provider_engine(cfg, _runtime())

    assert getattr(_timeout_record(cli_logs), "lgtmaybe_version", None) == "1.2.3"


def test_an_unreadable_version_does_not_break_the_run(monkeypatch) -> None:
    """A source checkout with no installed metadata still reviews."""
    import lgtmaybe.cli as cli_module

    def boom(_name: str) -> str:
        raise cli_module.metadata.PackageNotFoundError("lgtmaybe")

    monkeypatch.setattr(cli_module.metadata, "version", boom)
    assert cli_module.package_version() == "unknown"


def test_the_two_sources_are_distinguishable_at_the_same_value(cli_logs) -> None:
    """The whole point: a configured 600 and a default 600 must not look alike."""
    cloud_default = default_timeout_for(Provider.openai)
    cfg = ReviewConfig(provider=Provider.openai, model="m", timeout=cloud_default)
    build_provider_engine(cfg, _runtime())

    record = _timeout_record(cli_logs)
    assert getattr(record, "timeout_s", None) == cloud_default
    assert getattr(record, "timeout_source", None) == "configured"
