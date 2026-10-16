"""Secret redaction: scrub obvious secrets from patches before they reach the provider.

This is a defence against leaking credentials to a third-party LLM provider
(OWASP A02 *Cryptographic Failures* / A07 *Identification and Authentication
Failures* — secrets exposure). It is best-effort pattern matching, not a
guarantee; it complements, never replaces, keeping real secrets out of git.
"""

from __future__ import annotations

import re
from functools import lru_cache

REDACTED_PLACEHOLDER = "[REDACTED]"

# Simple patterns — the whole match is replaced wholesale.
_SIMPLE_PATTERNS: list[re.Pattern[str]] = [
    # AWS access key IDs (AKIA... 20 chars)
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # OpenAI keys: sk- followed by at least 20 word chars
    re.compile(r"sk-[A-Za-z0-9\-_]{20,}"),
    # GitHub tokens: ghp_, gho_, ghu_, ghs_, ghr_, ght_ followed by at least 20 word chars
    re.compile(r"gh[pousrt]_[A-Za-z0-9]{20,}"),
    # GitHub fine-grained PATs: github_pat_ followed by base62/underscore
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),
    # Slack tokens: xoxb-/xoxp-/xoxa-/xoxr-/xoxs- bot/user/app tokens
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    # Google API keys: AIza followed by 35 url-safe chars
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    # Stripe live/test secret keys
    re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{16,}"),
    # JSON Web Encryption (compact): header.encrypted_key.iv.ciphertext.tag —
    # five base64url segments. The second segment is an encrypted key, NOT a
    # base64url JSON header, so the three-part JWT pattern below never matches a
    # JWE; without this it would egress whole. Listed first so the 5-segment form
    # is consumed before the 3-segment pattern can claim a prefix of it.
    re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{8,}){4}"),
    # JSON Web Tokens: header.payload.signature, each base64url. The payload
    # carries claims/PII, so the whole token must go — not just up to a dot.
    re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    # npm automation/auth tokens: npm_ followed by 36 base62 chars.
    re.compile(r"npm_[A-Za-z0-9]{36,}"),
    # PyPI API tokens: pypi- followed by a long base64 macaroon.
    re.compile(r"pypi-[A-Za-z0-9_-]{16,}"),
    # GitLab PATs: glpat- followed by base62/underscore (GitLab is a forge we read).
    re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"),
]

# PEM private-key blocks (RSA/EC/OPENSSH/generic) — the one multi-line pattern,
# kept out of _SIMPLE_PATTERNS because its replacement must be line-count
# preserving: hunk expansion and diff hunk headers index into the redacted text
# by line number, so collapsing an N-line block into one placeholder desyncs
# (and can crash) everything downstream that counts lines.
#
# The body is matched line by line: each line is an optional diff marker and
# indent, then one unbroken base64 run or a `Proc-Type:`/`DEK-Info:` header. A
# character class over the whole body admitted letters and spaces, so fake
# markers could still hide ordinary code (`import os`) from the reviewer; and an
# unbounded `.*?` rescanned to end of text from every unterminated header. Lines
# may be split by real newlines, JSON/YAML-escaped `\n`, or (one-line keys in
# env files) a space before another long base64 run. Atomic groups keep every
# step linear on attacker input.
_PEM_KIND = r"(?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?"
_PEM_SEP = r"(?:(?>[ \t]*)(?:\r?\n|(?:\\r)?\\n)|(?>[ \t]+)(?=[A-Za-z0-9+/=]{16}))"
_PEM_INDENT = r"(?>[ \t]*)(?:[+-](?>[ \t]*))?"
_PEM_LINE = _PEM_INDENT + r"(?:(?>[A-Za-z0-9+/=]+)|(?:Proc-Type|DEK-Info):[^\n\\]*)?"
_PEM_PATTERN = re.compile(
    rf"-----BEGIN {_PEM_KIND}PRIVATE KEY-----"
    rf"(?:{_PEM_SEP}{_PEM_LINE})*?(?:{_PEM_SEP})?{_PEM_INDENT}"
    rf"-----END {_PEM_KIND}PRIVATE KEY-----"
)


def _replace_pem(m: re.Match[str]) -> str:
    """One placeholder per matched line, so the block's line count is preserved."""
    return "\n".join([REDACTED_PLACEHOLDER] * (m.group(0).count("\n") + 1))


def _replace_value(m: re.Match[str]) -> str:
    """Replace only the captured ``secret`` group, preserving the key name / scheme."""
    full = m.group(0)
    start = m.start("secret") - m.start()
    end = m.end("secret") - m.start()
    return full[:start] + REDACTED_PLACEHOLDER + full[end:]


# Capturing-group patterns — only the named ``secret`` group is replaced, so the
# surrounding key name / scheme stays readable in the diff.
#
# Shared key tail: an optional closing quote and dict-subscript bracket, then
# whitespace and the `=`/`:` separator. `==` is accepted too, so a spaced
# comparison against a literal (`password == "hunter2"`) redacts exactly like
# an assignment instead of leaking past the first `=`.
_KEY_TAIL = r"[\"']?\]?\s*(?:==|[:=])\s*"

_VALUE_PATTERNS: list[re.Pattern[str]] = [
    # Generic high-entropy assignments: api_key = "..." or token = "..." (value ≥ 16 chars).
    # Keys may be dict-subscripted, so an optional closing quote and bracket sit
    # between the key name and the `=`: headers["X-Api-Key"] = "...".
    re.compile(
        r"(?i)(?:api[_\-]?key|api[_\-]?secret|secret[_\-]?access[_\-]?key|"
        r"session[_\-]?token|access[_\-]?token|secret[_\-]?key|"
        r"client[_\-]?secret|private[_\-]?key|token|secret)"
        + _KEY_TAIL
        + r"[\"']?(?P<secret>[A-Za-z0-9\-_/+=]{16,})[\"']?"
    ),
    # Quoted password / passphrase literals: password = "hunter2" (value ≥ 4 chars).
    # Quotes required so we don't swallow prose like "password reset"; the key may
    # be quoted too (JSON), or dict-subscripted (headers["X-Password"]). The value
    # never crosses a line end: an unterminated quote must not swallow the following
    # diff lines up to the next quote. An escaped quote is part of the value, not
    # its end, or the tail would leak.
    re.compile(
        r"(?i)(?:password|passwd|passphrase|pwd)"
        + _KEY_TAIL
        + r"(?P<q>[\"'])(?P<secret>(?:[^\"'\n\\]|\\.){4,})(?P=q)"
    ),
    # Unquoted connection-string passwords: Server=db;Password=hunter2;User ID=sa
    # (ADO.NET / JDBC). The value is bare and ends at `;`, a quote, whitespace or
    # line end. Parens and brackets may appear inside the value, but it must not
    # END with one: calls (`password = get_password()`) and subscript copies
    # (`password = payload["password"]`) do, and are ordinary code, while a
    # literal like `Password=Tr0ub(4)dor;` still scrubs. Identifier copies
    # (`x.password = user.password;`, `password = hashed_password`) are also
    # ordinary code, so the guard below skips only reference-shaped values: the
    # whole value is identifier-like and `password`/`passwd`/`pwd` sits in it as
    # its own dot/underscore component. A literal that merely embeds the word
    # (`Password=hunter2password;`) has no such separator and still redacts.
    re.compile(
        r"(?i)(?:password|passwd|pwd)"
        + _KEY_TAIL
        + r"(?!(?:[A-Za-z0-9_.]*[._])?(?:password|passwd|pwd)"
        r"(?:[._][A-Za-z0-9_.]*)?(?=[;\"'\s]|$))"
        r"(?P<secret>[^\s;\"\']{3,}[^\s;\"\'()\[\]])(?=[;\"'\s]|$)"
    ),
    # Authorization headers carrying a Bearer/Basic credential. Tolerates a quoted
    # key and a quoted value, e.g. JSON `"Authorization": "Bearer <token>"`, or a
    # dict subscript: headers["Authorization"] = "Bearer ...".
    re.compile(
        r"(?i)[\"']?authorization" + _KEY_TAIL + r"[\"']?(?:bearer|basic)\s+"
        r"(?P<secret>[A-Za-z0-9\-._~+/=]{16,})"
    ),
    # Credentials embedded in connection-string URLs: scheme://user:secret@host.
    # The scheme length is bounded (schemes are short) so the leading character
    # class can't rescan a long run at every start offset — an unbounded `*` here
    # is quadratic (ReDoS) on a long credential-free line, e.g. a base64 blob.
    re.compile(r"(?i)[a-z][a-z0-9+.\-]{0,31}://[^:/?#\s]+:(?P<secret>[^@/?#\s]{4,})@"),
    # Azure storage / Cosmos connection strings: ...;AccountKey=<base64>;...
    # Only the key value is scrubbed; AccountName/EndpointSuffix stay readable.
    re.compile(r"(?i)(?:Account|Shared(?:Access)?)Key\s*=\s*(?P<secret>[A-Za-z0-9/+]{32,}={0,2})"),
]


@lru_cache(maxsize=128)
def redact(text: str) -> str:
    """Return *text* with known secret patterns replaced by REDACTED_PLACEHOLDER.

    Memoized: the same file head text is redacted at several pipeline stages
    (hunk expansion, reflection grounding, every deferral re-judge), and each
    pass runs the full pattern battery over the whole input. Bounded so the
    cache can't retain an unbounded number of large file texts.
    """
    text = _PEM_PATTERN.sub(_replace_pem, text)
    for pattern in _SIMPLE_PATTERNS:
        text = pattern.sub(REDACTED_PLACEHOLDER, text)
    for pattern in _VALUE_PATTERNS:
        text = pattern.sub(_replace_value, text)
    return text
