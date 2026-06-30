"""Tests for redact.py — secret scrubbing before diffs leave for the provider."""

from __future__ import annotations

from lgtmaybe.engine.redact import REDACTED_PLACEHOLDER, redact

_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
_OPENAI_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGHIJKLMNOP"
_GITHUB_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz123456"
_API_KEY_ASSIGN = 'api_key = "supersecretvalue12345678901234567"'
_TOKEN_ASSIGN = 'token = "myverysecrettoken12345678901234567"'


def test_aws_key_redacted() -> None:
    diff = f"+AWS_ACCESS_KEY_ID={_AWS_KEY}\n"
    result = redact(diff)
    assert _AWS_KEY not in result
    assert REDACTED_PLACEHOLDER in result


def test_openai_key_redacted() -> None:
    diff = f"+OPENAI_API_KEY={_OPENAI_KEY}\n"
    result = redact(diff)
    assert _OPENAI_KEY not in result
    assert REDACTED_PLACEHOLDER in result


def test_github_token_redacted() -> None:
    diff = f"+GH_TOKEN={_GITHUB_TOKEN}\n"
    result = redact(diff)
    assert _GITHUB_TOKEN not in result
    assert REDACTED_PLACEHOLDER in result


def test_generic_api_key_assignment_redacted() -> None:
    diff = f"+{_API_KEY_ASSIGN}\n"
    result = redact(diff)
    assert "supersecretvalue" not in result
    assert REDACTED_PLACEHOLDER in result


def test_non_secret_content_preserved() -> None:
    diff = "@@ -1,3 +1,4 @@\n context\n+added normal line\n"
    result = redact(diff)
    assert "added normal line" in result
    assert REDACTED_PLACEHOLDER not in result


def test_multiple_secrets_all_redacted() -> None:
    diff = f"+AWS_KEY={_AWS_KEY}\n+OPENAI={_OPENAI_KEY}\n"
    result = redact(diff)
    assert _AWS_KEY not in result
    assert _OPENAI_KEY not in result


def test_redact_returns_string() -> None:
    assert isinstance(redact("no secrets here"), str)


# ---------------------------------------------------------------------------
# Broader secret classes (OWASP A02/A07 — secrets must not egress to the LLM)
# ---------------------------------------------------------------------------
# These fixtures are assembled from fragments at runtime so the contiguous
# token literal never appears in source — that keeps push-protection / secret
# scanners from flagging obviously-fake test data, while redact() still sees the
# full string.

_GITHUB_FINE_GRAINED = "github_pat" + "_11ABCDEFG0abcdefghijkl_mnopqrstuvwxyz0123456789ABCDEFXY"
_SLACK_TOKEN = "xox" + "b-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx"
_GOOGLE_API_KEY = "AIza" + "SyA1234567890abcdefghijklmnopqrstuvw"
_STRIPE_KEY = "sk_" + "live_" + "abcdefghijklmnop1234567890"
_PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAtnotarealkeyjustfortestingpurposes1234567890\n"
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n"
    "-----END RSA PRIVATE KEY-----"
)


def test_github_fine_grained_pat_redacted() -> None:
    result = redact(f"+TOKEN={_GITHUB_FINE_GRAINED}\n")
    assert _GITHUB_FINE_GRAINED not in result
    assert REDACTED_PLACEHOLDER in result


def test_slack_token_redacted() -> None:
    result = redact(f"+SLACK_BOT_TOKEN={_SLACK_TOKEN}\n")
    assert _SLACK_TOKEN not in result
    assert REDACTED_PLACEHOLDER in result


def test_google_api_key_redacted() -> None:
    result = redact(f"+GOOGLE_API_KEY={_GOOGLE_API_KEY}\n")
    assert _GOOGLE_API_KEY not in result
    assert REDACTED_PLACEHOLDER in result


def test_stripe_secret_key_redacted() -> None:
    result = redact(f"+STRIPE_KEY={_STRIPE_KEY}\n")
    assert _STRIPE_KEY not in result
    assert REDACTED_PLACEHOLDER in result


def test_pem_private_key_block_redacted() -> None:
    result = redact("+" + _PRIVATE_KEY + "\n")
    assert "MIIEowIBAAKCAQEA" not in result
    assert REDACTED_PLACEHOLDER in result


def test_quoted_password_literal_redacted() -> None:
    result = redact('+    password = "hunter2pass"\n')
    assert "hunter2pass" not in result
    assert REDACTED_PLACEHOLDER in result
    # The key name survives so the reviewer still sees what was assigned.
    assert "password" in result


def test_password_prose_not_redacted() -> None:
    """Plain English mentioning a password must not trip the redactor."""
    result = redact("+# Send the user a password reset email when requested\n")
    assert REDACTED_PLACEHOLDER not in result
    assert "password reset" in result


def test_authorization_bearer_header_redacted() -> None:
    secret = "eyJ" + "hbGciOiJIUzI1Niexampletokenvalue0123456789"
    result = redact(f'+    "Authorization": "Bearer {secret}",\n')
    assert secret not in result
    assert REDACTED_PLACEHOLDER in result
    # Scheme word is preserved; only the credential is scrubbed.
    assert "Bearer" in result


def test_connection_string_password_redacted() -> None:
    result = redact("+DATABASE_URL=postgres://admin:s3cr3tDbPass@db.internal:5432/app\n")
    assert "s3cr3tDbPass" not in result
    assert REDACTED_PLACEHOLDER in result
    # Host stays visible — only the password segment is scrubbed.
    assert "db.internal" in result
    assert "admin" in result


def test_value_pattern_preserves_key_name() -> None:
    """Generic assignment redaction keeps the identifier, scrubs only the value."""
    result = redact('+api_key = "abcdefghijklmnop1234567890"\n')
    assert "api_key" in result
    assert "abcdefghijklmnop1234567890" not in result


def test_idempotent_on_already_redacted_text() -> None:
    once = redact(f"+SLACK={_SLACK_TOKEN}\n")
    twice = redact(once)
    assert once == twice


# ---------------------------------------------------------------------------
# Additional secret classes (JWTs, npm/PyPI tokens, Azure storage keys)
# ---------------------------------------------------------------------------
# Assembled from fragments so the contiguous literal never appears in source.

_JWT = (
    "eyJ" + "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIiwiZW1haWwiOiJhQGIuY29tIn0"
    ".dozjgNryP4J3jVmNHl0w5N" + "_XgL0n3I9PlFUP0K1Yc"
)
_NPM_TOKEN = "npm_" + "abcdefghijklmnopqrstuvwxyz0123456789AB"
_PYPI_TOKEN = "pypi-" + "AgEIcHlwaS5vcmcCJD%s" % ("abcd1234" * 6)
_AZURE_ACCOUNT_KEY = "ABCDdef123" * 6 + "ab=="


def test_jwt_redacted() -> None:
    """A full three-segment JWT (header.payload.signature) must be scrubbed whole."""
    result = redact(f"+const token = '{_JWT}';\n")
    assert _JWT not in result
    # The payload segment carries claims/PII — none of it may survive.
    assert "zdWIiOiIxMjM0" not in result
    assert REDACTED_PLACEHOLDER in result


_JWE = (
    "eyJ" + "hbGciOiJSU0EtT0FFUCIsImVuYyI6IkEyNTZHQ00ifQ"  # protected header (eyJ…)
    ".OKOawDo13gRp2ojaHV7LFpZcgV7T6DVZKTyKOMTYUmKoTCVJRgckCL9kiMT03JGe"  # encrypted key
    ".48V1_ALb6US04U3b"  # iv
    ".5eym8TW_c8SuK0ltJ3rpYIzOeDQz7TALvtu6UG9oMo4vpzs9tX_EFShS8iB7j6ji"  # ciphertext
    ".XFBoMYUZodetZdvTiFvSkQ"  # auth tag
)


def test_jwe_compact_token_redacted() -> None:
    """A five-segment compact JWE (common with cloud OIDC/Entra) must be scrubbed.

    The three-segment JWT pattern can't match a JWE — its second segment is an
    encrypted key, not a base64url JSON header (`eyJ…`) — so without a dedicated
    pattern the whole token would egress. Framed without an assignment keyword so
    the generic value pattern can't take credit by scrubbing only the first
    segment; the middle (ciphertext) segment must be gone for this to pass.
    """
    result = redact(f"+  cookie {_JWE}\n")
    assert _JWE not in result
    # A middle segment — not just the leading header — must be scrubbed.
    assert "5eym8TW_c8SuK0lt" not in result
    assert REDACTED_PLACEHOLDER in result


def test_jwt_still_redacted_alongside_jwe_pattern() -> None:
    """The new JWE pattern must not stop a plain three-segment JWT being scrubbed."""
    result = redact(f"+  cookie {_JWT}\n")
    assert _JWT not in result
    # The payload segment carries claims/PII — none of it may survive.
    assert "zdWIiOiIxMjM0" not in result
    assert REDACTED_PLACEHOLDER in result


def test_npm_token_redacted() -> None:
    result = redact(f"+//registry.npmjs.org/:_authToken={_NPM_TOKEN}\n")
    assert _NPM_TOKEN not in result
    assert REDACTED_PLACEHOLDER in result


def test_pypi_token_redacted() -> None:
    result = redact(f"+TWINE_PASSWORD={_PYPI_TOKEN}\n")
    assert _PYPI_TOKEN not in result
    assert REDACTED_PLACEHOLDER in result


def test_azure_storage_account_key_redacted() -> None:
    conn = (
        "DefaultEndpointsProtocol=https;AccountName=devstore;"
        f"AccountKey={_AZURE_ACCOUNT_KEY};EndpointSuffix=core.windows.net"
    )
    result = redact(f"+AZURE_STORAGE_CONNECTION_STRING={conn}\n")
    assert _AZURE_ACCOUNT_KEY not in result
    assert REDACTED_PLACEHOLDER in result
    # Non-secret structure stays readable for the reviewer.
    assert "AccountName=devstore" in result


def test_connection_string_pattern_is_not_quadratic() -> None:
    """A long credential-free line must not blow up the connection-string regex.

    The diff is attacker-controlled on a fork PR; with an unbounded scheme the
    pattern was O(n^2) (~18s for 100k chars), stalling every review. Bounding the
    scheme keeps it linear. A generous bound stays robust on a slow CI box.
    """
    import time

    pathological = "x://" + "a" * 200_000  # scheme-like prefix, no credentials
    start = time.perf_counter()
    redact(pathological)
    assert time.perf_counter() - start < 2.0
