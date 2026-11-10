"""Structured logging: valid JSON lines, secrets redacted."""

from __future__ import annotations

import io
import json
import logging
import sys

import pytest

from lgtmaybe.core.logging import JsonFormatter, PlainFormatter, get_logger, register_secret


def _record(msg: str, *args: object, **extra: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="lgtmaybe",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_log_line_is_valid_json() -> None:
    out = JsonFormatter().format(_record("hello %s", "world"))
    data = json.loads(out)
    assert data["message"] == "hello world"
    assert data["level"] == "INFO"
    assert data["logger"] == "lgtmaybe"


def test_secret_is_redacted() -> None:
    register_secret("sk-supersecret-123")
    out = JsonFormatter().format(_record("auth header: sk-supersecret-123"))
    assert "sk-supersecret-123" not in out
    assert "***REDACTED***" in out
    assert json.loads(out)  # still valid json after redaction


def test_extras_are_redacted_and_serialised() -> None:
    register_secret("topsecret")
    rec = _record("posting review")
    rec.repo = "lgtmaybe/lgtmaybe"
    rec.token = "topsecret"
    data = json.loads(JsonFormatter().format(rec))
    assert data["repo"] == "lgtmaybe/lgtmaybe"
    assert data["token"] == "***REDACTED***"


def test_get_logger_attaches_single_json_handler() -> None:
    logger = get_logger("lgtmaybe.test")
    get_logger("lgtmaybe.test")  # idempotent
    json_handlers = [h for h in logger.handlers if isinstance(h.formatter, JsonFormatter)]
    assert len(json_handlers) == 1


def test_plain_line_reads_as_text_with_its_extras() -> None:
    rec = _record("reviewing %s", "lens")
    rec.lens = "security"
    rec.batch = 2

    assert PlainFormatter().format(rec) == "lgtmaybe: reviewing lens (lens=security, batch=2)"


def test_plain_line_flags_warnings_and_redacts_secrets() -> None:
    register_secret("sk-plain-secret")
    rec = _record("auth header: sk-plain-secret")
    rec.levelno, rec.levelname = logging.WARNING, "WARNING"
    rec.token = "sk-plain-secret"

    out = PlainFormatter().format(rec)

    assert out.startswith("lgtmaybe: warning: auth header: ***REDACTED***")
    assert "sk-plain-secret" not in out


def test_plain_line_redacts_tracebacks() -> None:
    register_secret("sk-plain-secret")
    rec = _record("review failed")
    try:
        raise ValueError("token sk-plain-secret")
    except ValueError:
        rec.exc_info = sys.exc_info()

    out = PlainFormatter().format(rec)

    assert "ValueError: token ***REDACTED***" in out
    assert "sk-plain-secret" not in out


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def _formatter_on(stream: io.StringIO, monkeypatch: pytest.MonkeyPatch, name: str) -> object:
    monkeypatch.setattr("sys.stderr", stream)
    return get_logger(name).handlers[0].formatter


def test_a_terminal_gets_plain_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    # A person watching a local review reads progress, not CI's JSON.
    monkeypatch.delenv("LGTMAYBE_LOG_FORMAT", raising=False)

    assert isinstance(_formatter_on(_Terminal(), monkeypatch, "lgtmaybe.tty"), PlainFormatter)


def test_a_pipe_keeps_json_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    # CI stderr is not a terminal, so the Action's logs are unchanged.
    monkeypatch.delenv("LGTMAYBE_LOG_FORMAT", raising=False)

    formatter = _formatter_on(io.StringIO(), monkeypatch, "lgtmaybe.pipe")

    assert type(formatter) is JsonFormatter


def test_log_format_env_overrides_the_terminal_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LGTMAYBE_LOG_FORMAT", "json")
    on_terminal = _formatter_on(_Terminal(), monkeypatch, "lgtmaybe.forced-json")
    monkeypatch.setenv("LGTMAYBE_LOG_FORMAT", "plain")
    on_pipe = _formatter_on(io.StringIO(), monkeypatch, "lgtmaybe.forced-plain")

    assert type(on_terminal) is JsonFormatter
    assert isinstance(on_pipe, PlainFormatter)
