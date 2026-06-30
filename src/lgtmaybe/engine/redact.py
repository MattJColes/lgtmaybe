"""Secret redaction: scrub obvious secrets from patches before they reach the provider.

This is a defence against leaking credentials to a third-party LLM provider
(OWASP A02 *Cryptographic Failures* / A07 *Identification and Authentication
Failures* — secrets exposure). It is best-effort pattern matching, not a
guarantee; it complements, never replaces, keeping real secrets out of git.
"""

from __future__ import annotations

import re

REDACTED_PLACEHOLDER = "[REDACTED]"

# Simple patterns — the whole match is replaced wholesale.
_SIMPLE_PATTERNS: list[re.Pattern[str]] = [
    # AWS access key IDs (AKIA... 20 chars)
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # OpenAI keys: sk- followed by at least 20 word chars
    re.compile(r"sk-[A-Za-z0-9\-_]{20,}"),
    # GitHub tokens: ghp_, gho_, ghs_, ghr_, ght_ followed by at least 20 word chars
    re.compile(r"gh[poshrt]_[A-Za-z0-9]{20,}"),
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
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{8,}){4}"),
    # JSON Web Tokens: header.payload.signature, each base64url. The payload
    # carries claims/PII, so the whole token must go — not just up to a dot.
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    # npm automation/auth tokens: npm_ followed by 36 base62 chars.
    re.compile(r"npm_[A-Za-z0-9]{36,}"),
    # PyPI API tokens: pypi- followed by a long base64 macaroon.
    re.compile(r"pypi-[A-Za-z0-9_-]{16,}"),
    # PEM private-key blocks (RSA/EC/OPENSSH/generic) — match the whole block.
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
        r".*?-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
        re.DOTALL,
    ),
]

# Capturing-group patterns — only the named ``secret`` group is replaced, so the
# surrounding key name / scheme stays readable in the diff.
_VALUE_PATTERNS: list[re.Pattern[str]] = [
    # Generic high-entropy assignments: api_key = "..." or token = "..." (value ≥ 16 chars)
    re.compile(
        r"(?i)(?:api[_\-]?key|api[_\-]?secret|access[_\-]?token|secret[_\-]?key|token)"
        r'\s*[=:]\s*["\']?(?P<secret>[A-Za-z0-9\-_/+=]{16,})["\']?'
    ),
    # Quoted password / passphrase literals: password = "hunter2" (value ≥ 4 chars).
    # Quotes required so we don't swallow prose like "password reset".
    re.compile(
        r"(?i)(?:password|passwd|passphrase|pwd)\s*[=:]\s*"
        r"(?P<q>[\"'])(?P<secret>[^\"']{4,})(?P=q)"
    ),
    # Authorization headers carrying a Bearer/Basic credential. Tolerates a quoted
    # key and a quoted value, e.g. JSON `"Authorization": "Bearer <token>"`.
    re.compile(
        r"(?i)[\"']?authorization[\"']?\s*[=:]\s*[\"']?(?:bearer|basic)\s+"
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


def _replace_value(m: re.Match[str]) -> str:
    """Replace only the captured ``secret`` group, preserving the key name / scheme."""
    full = m.group(0)
    value = m.group("secret")
    return full.replace(value, REDACTED_PLACEHOLDER, 1)


def redact(text: str) -> str:
    """Return *text* with known secret patterns replaced by REDACTED_PLACEHOLDER."""
    for pattern in _SIMPLE_PATTERNS:
        text = pattern.sub(REDACTED_PLACEHOLDER, text)
    for pattern in _VALUE_PATTERNS:
        text = pattern.sub(_replace_value, text)
    return text
