"""Bearer token issue, lookup and revocation. Only the sha256 hash is ever stored."""
import hashlib
import secrets
from datetime import timedelta

from django.db import transaction

from accounts.models import ApiToken
from core.clock import now

TOKEN_PREFIX = "vd_"
HASH_LENGTH = 12  # indexed lookup prefix


def hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_plaintext() -> str:
    """vd_ + 40 url-safe characters (30 random bytes encode to exactly 40)."""
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(30)}"


@transaction.atomic
def issue_token(user, name: str, plaintext: str | None = None, is_demo: bool = False):
    """Create a token and return (token_row, plaintext). The plaintext is shown once."""
    plaintext = plaintext or generate_plaintext()
    token = ApiToken.objects.create(
        user=user,
        name=name,
        prefix=plaintext[:HASH_LENGTH],
        key_hash=hash_token(plaintext),
        is_demo=is_demo,
    )
    return token, plaintext


def authenticate_token(plaintext: str | None):
    """Return the user for a live token, else None.

    last_used_at is written at most once a minute so a busy client does not turn
    every API call into a write.
    """
    if not plaintext or not plaintext.startswith(TOKEN_PREFIX):
        return None
    token = ApiToken.objects.select_related("user").filter(key_hash=hash_token(plaintext)).first()
    if token is None or token.revoked_at is not None:
        return None
    user = token.user
    if not user.is_active:
        return None
    if token.last_used_at is None or (now() - token.last_used_at) >= timedelta(minutes=1):
        token.last_used_at = now()
        ApiToken.objects.filter(pk=token.pk).update(last_used_at=token.last_used_at)
    return user


@transaction.atomic
def revoke_token(token: ApiToken) -> ApiToken:
    """Revoke a token (idempotent)."""
    ApiToken.objects.filter(pk=token.pk, revoked_at__isnull=True).update(revoked_at=now())
    token.refresh_from_db()
    return token
