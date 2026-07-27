"""A deterministic OpenAI-compatible stub server for hermetic CLI e2e tests.

``test_local_providers.py`` proves the CLI works against a *real* local model,
which needs a downloaded model and answers differently every run. This is the
complement: an in-process server that speaks just enough of
``POST /v1/chat/completions`` for litellm's ``openai/`` route, so
``--provider openai-compatible`` drives the entire pipeline — git-diff
resolution, redaction, injection wrapping, compression and batching, parsing,
re-anchoring, dedupe, reflection, filtering, rendering — with only the model's
judgement stubbed out.

Rather than answer from a fixed script, the stub reads **planted markers** out
of the diff it is actually sent, so every scenario has exact ground truth. Put
this in a line of a fixture file::

    value = compute()  # @flag sev=high title=Something is wrong

and the review call that sees it returns a finding on that line. Extra keys tune
what the "model" gets wrong, which is how the anchoring behaviour is tested:

- ``anchor=exact`` (default) — return the real line text; the engine must snap
- ``anchor=bogus`` — return text matching nothing; the finding is unanchored
- ``anchor=none``  — omit the anchor; the engine trusts the model's line
- ``line=off``     — return a deliberately wrong line number

The reflection audit is answered too: keep everything at confidence 8, unless a
title contains ``DROPME`` (dropped) or ``LOWCONF`` (scored 3). A ``:`` suffix on
the model name switches the response shape — ``mock:prose`` wraps the JSON in
conversational text, ``mock:think`` prefixes a ``<think>`` block, ``mock:junk``
answers with unusable prose — which is how the lenient parser is exercised.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_FLAG_RE = re.compile(r"@flag\s+(?P<spec>[^\n]*)")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")
_FILE_RE = re.compile(r"^\+\+\+ b/(?P<path>.+)$")
_SEVERITIES = frozenset({"info", "low", "medium", "high", "critical"})

# The one phrase unique to the reflection system prompt. Routing on a looser
# word ("verdicts") misfires: the static-analysis hints preamble contains it.
_AUDIT_MARKER = "auditing another reviewer's findings"
_FINDINGS_MARKER = "Findings (indexed from 0):"


class StubServer:
    """A running stub, plus the prompts it has been sent."""

    def __init__(self, httpd: ThreadingHTTPServer, calls: list[dict[str, Any]]) -> None:
        self._httpd = httpd
        self.calls = calls

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"

    @property
    def prompts(self) -> str:
        """Every prompt the stub has received, joined — for egress assertions."""
        return "\n".join(call["prompt"] for call in self.calls)

    def reset(self) -> None:
        self.calls.clear()

    def shutdown(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def start_stub(port: int = 0) -> StubServer:
    """Start the stub on *port* (0 = any free port) in a daemon thread."""
    calls: list[dict[str, Any]] = []
    lock = threading.Lock()

    class Handler(_BaseHandler):
        _calls = calls
        _lock = lock

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return StubServer(httpd, calls)


def _parse_spec(raw: str) -> dict[str, str]:
    """Parse ``k=v k=v title=rest`` — ``title`` swallows the remainder of the line."""
    spec: dict[str, str] = {}
    rest = raw.strip()
    while rest:
        match = re.match(r"(\w+)=", rest)
        if match is None:
            break
        key = match.group(1)
        rest = rest[match.end() :]
        if key == "title":
            spec[key] = rest.strip()
            break
        value, _, rest = rest.partition(" ")
        spec[key] = value
        rest = rest.strip()
    return spec


def _prompt_text(messages: list[dict[str, Any]]) -> str:
    """Flatten every message's content, string or content-block form."""
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(
                block["text"]
                for block in content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            )
    return "\n".join(parts)


def _findings_from(prompt: str) -> list[dict[str, Any]]:
    """Walk the diff in *prompt*, turning each planted ``@flag`` into a finding."""
    findings: list[dict[str, Any]] = []
    path = ""
    new_line = 0
    for raw in prompt.splitlines():
        file_match = _FILE_RE.match(raw)
        if file_match:
            path, new_line = file_match.group("path"), 0
            continue
        hunk_match = _HUNK_RE.match(raw)
        if hunk_match:
            new_line = int(hunk_match.group("new"))
            continue
        if raw.startswith("-"):
            continue
        if raw.startswith("+"):
            content = raw[1:]
            flag = _FLAG_RE.search(content)
            if flag and path:
                findings.append(_finding(path, new_line, content, _parse_spec(flag.group("spec"))))
        new_line += 1
    return findings


def _finding(path: str, line: int, content: str, spec: dict[str, str]) -> dict[str, Any]:
    anchor: str | None = content
    if spec.get("anchor") == "bogus":
        anchor = "this text appears nowhere in the diff at all"
    elif spec.get("anchor") == "none":
        anchor = None
    severity = spec.get("sev", "medium")
    title = spec.get("title", "planted finding")
    return {
        "path": path,
        "line": line + 37 if spec.get("line") == "off" else line,
        "side": "RIGHT",
        "severity": severity if severity in _SEVERITIES else "medium",
        "title": title,
        "body": f"Planted marker on `{path}`: {title}.",
        "failure_scenario": (
            "Trigger: the marked line runs. Change: stubbed. Impact: the test asserts on it."
        ),
        "suggestion": None,
        "anchor": anchor,
    }


def _verdicts_for(prompt: str) -> str:
    """Answer the reflection audit from the findings JSON it was handed."""
    verdicts = []
    for index, finding in enumerate(_findings_in_audit(prompt)):
        title = str(finding.get("title", "")) if isinstance(finding, dict) else ""
        verdicts.append(
            {
                "index": index,
                "keep": "DROPME" not in title,
                "confidence": 3 if "LOWCONF" in title else 8,
                "broad": "BROAD" in title,
                "needs": [],
            }
        )
    return json.dumps({"verdicts": verdicts})


def _findings_in_audit(prompt: str) -> list[Any]:
    start = prompt.find("[", prompt.find(_FINDINGS_MARKER))
    if start <= 0:
        return []
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(prompt)):
        char = prompt[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(prompt[start : index + 1])
                except json.JSONDecodeError:
                    return []
    return []


def _answer(prompt: str) -> str:
    if _AUDIT_MARKER in prompt:
        return _verdicts_for(prompt)
    if "mermaid" in prompt.lower():
        return json.dumps(
            {
                "mermaid": 'C4Context\n  title Stub\n  Person(u, "User")',
                "ascii": "[User] -> [Thing]",
                "summary": "stub diagram",
            }
        )
    return json.dumps({"findings": _findings_from(prompt)})


def _shape(content: str, mode: str) -> str:
    """Re-shape a clean JSON answer the way a messier model would emit it."""
    if mode == "prose":
        return f"Sure! Here is what I found [a, b, c]:\n```json\n{content}\n```\nHope that helps."
    if mode == "think":
        return f"<think>let me count the lines [1] [2]</think>\n{content}"
    if mode == "junk":
        return "I could not analyse this diff. Please try again later."
    return content


class _BaseHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    _calls: list[dict[str, Any]] = []
    _lock = threading.Lock()

    def log_message(self, *_args: Any) -> None:
        """Silence the default stderr access log."""

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's naming
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        model = str(payload.get("model", ""))
        prompt = _prompt_text(payload.get("messages", []))
        with self._lock:
            self._calls.append({"model": model, "prompt": prompt})

        content = _shape(_answer(prompt), model.partition(":")[2])
        body = json.dumps(
            {
                "id": "chatcmpl-stub",
                "object": "chat.completion",
                "created": 0,
                "model": model or "stub",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
