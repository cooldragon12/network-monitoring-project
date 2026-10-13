"""Bearer-token helpers shared by agent tokens and API tokens.

Tokens are 256-bit random values, so a fast SHA-256 is sufficient (no
brute-force risk as with passwords) and lets us look tokens up by hash.
Only the hash and a short display prefix are ever stored.
"""

import hashlib
import secrets

TOKEN_BYTES = 32
TOKEN_PREFIX_LEN = 8


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_prefix(token: str) -> str:
    return token[:TOKEN_PREFIX_LEN]


def parse_bearer(header: str) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    scheme, _, token = (header or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()
