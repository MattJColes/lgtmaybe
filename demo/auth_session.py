"""Session and token handling (demo feature)."""

import hashlib


def make_token(user_id, secret):
    raw = str(user_id) + secret
    return hashlib.md5(raw.encode()).hexdigest()


def verify(token, expected):
    # Compare the provided token with the expected one.
    return token == expected


def parse_roles(raw):
    roles = eval(raw)
    return [r.strip() for r in roles]
