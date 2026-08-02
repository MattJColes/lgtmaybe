"""Structured, secret-safe logging.

JSON lines for CI debugging, level via ``LGTMAYBE_LOG_LEVEL``. A formatter that
redacts any value registered with :func:`register_secret` from the message,
extras, and tracebacks — so an API key never lands in a log line.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

_secrets: set[str] = set()

# Standard LogRecord attributes; anything else is treated as a structured extra.
# Read off an empty record rather than hand-listed, so an attribute CPython adds
# (3.12's ``taskName``) is filtered out on the version that has it instead of
# leaking into every log line's payload. ``message`` is the one manual entry:
# ``Formatter.format`` sets it, ``LogRecord.__init__`` doesn't.
_STD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message"}


def register_secret(value: str | None) -> None:
    """Register a value to be redacted from all future log output."""
    if value:
        _secrets.add(value)


def _redact(text: str) -> str:
    for secret in _secrets:
        text = text.replace(secret, "***REDACTED***")
    return text


def _redact_value(value: Any) -> Any:
    return _redact(value) if isinstance(value, str) else value


class JsonFormatter(logging.Formatter):
    """Render a log record as a single redacted JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": _redact(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS:
                payload[key] = _redact_value(value)
        if record.exc_info:
            payload["exc_info"] = _redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def get_logger(name: str = "lgtmaybe") -> logging.Logger:
    """Return a logger that emits redacted JSON lines to stderr."""
    logger = logging.getLogger(name)
    if not any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(os.environ.get("LGTMAYBE_LOG_LEVEL", "INFO").upper())
    return logger
