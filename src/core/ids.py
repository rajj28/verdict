"""Public identifier generation.

Integer PKs are never exposed (BUILD-SEC section 2.8): every object gets a
prefix + "_" + 10 lowercase base32 characters.
"""
import secrets

# base32 lowercase without padding: 10 chars of it is ~50 bits of entropy.
_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_LENGTH = 10


def new_public_id(prefix: str) -> str:
    """Return e.g. new_public_id("prj") -> "prj_k3m9x2qa7d"."""
    body = "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))
    return f"{prefix}_{body}"
