"""Users, API tokens and authentication.

Writes and business rules live here. Views and API classes only call in here.
Every write is guarded (transaction + select_for_update, rules re-checked after
the lock) and audited inside the same transaction (BUILD-SEC section 2).
"""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction

import audit.services
from accounts.models import PasswordResetToken, User
from accounts.policy import can_manage_users, demo_account_for, visible_tokens
from accounts.tokens import issue_token, revoke_token
from core.clock import now
from core.errors import ApiError

INVALID_CREDENTIALS = "Email or password is incorrect."
RESET_TOKEN_TTL = timedelta(hours=24)
MIN_PASSWORD_LENGTH = 8
SESSION_MODE_KEY = "verdict_session_mode"
_dummy_hash: str | None = None


def _normalized_email(email: str) -> str:
    return (email or "").strip().lower()


def _email_errors(email: str) -> dict:
    errors = {}
    if not email:
        errors["email"] = ["An email address is required."]
    else:
        try:
            validate_email(email)
        except DjangoValidationError:
            errors["email"] = ["Enter a valid email address."]
    return errors


def _password_errors(password: str, field: str = "password") -> dict:
    """Length plus Django's own checks, reported as field errors for the form."""
    if not password:
        return {field: ["A password is required."]}
    if len(password) < MIN_PASSWORD_LENGTH:
        return {field: [f"Use at least {MIN_PASSWORD_LENGTH} characters."]}
    try:
        validate_password(password)
    except DjangoValidationError as error:
        return {field: list(error.messages)}
    return {}


def _audit_password(user: User, action: str, summary: str) -> None:
    """Passwords never reach the log: only who changed and when."""
    audit.services.record(user, action, target=user, summary=summary,
                          data={"public_id": user.public_id})


# --- registration and sign-in -------------------------------------------


@transaction.atomic
def register_user(*, email: str, password: str, display_name: str = "") -> User:
    """Create an account. Email is the login identifier and is unique."""
    email = _normalized_email(email)
    display_name = (display_name or "").strip()
    errors: dict = {}
    errors.update(_email_errors(email))
    errors.update(_password_errors(password))
    if errors:
        raise ApiError("invalid", "Please correct the highlighted fields.", 400, errors)
    if User.objects.filter(email=email).exists():
        raise ApiError("email_taken", "That email address already has an account.", 400,
                       {"email": ["That email address already has an account."]})
    user = User(email=email, display_name=display_name or email.split("@")[0])
    user.set_password(password)
    user.save()
    audit.services.record(user, "user.registered", target=user,
                          summary=f"{user.display_name} created an account.",
                          data={"public_id": user.public_id})
    return user


def authenticate(email: str, password: str) -> User | None:
    """The user behind these credentials, or None.

    A miss costs the same work as a hit: an unknown email still runs one password
    hash, so response time does not tell an attacker which addresses exist.
    """
    global _dummy_hash
    email = _normalized_email(email)
    user = User.objects.filter(email=email).first()
    if user is None or not user.is_active or not user.has_usable_password():
        if _dummy_hash is None:
            _dummy_hash = make_password(secrets.token_urlsafe(16))
        check_password(password or "", _dummy_hash)
        return None
    if not check_password(password or "", user.password):
        return None
    # The published demo secret must stop working as soon as demo mode is off,
    # including before bootstrap has retired credentials from an existing volume.
    if not settings.DEMO_MODE and password == "verdict-demo":
        return None
    return user


def start_session(request, user: User) -> None:
    """Log the user in. The session id is rotated to defeat fixation."""
    django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session[SESSION_MODE_KEY] = "demo" if settings.DEMO_MODE else "production"


def end_session(request) -> None:
    django_logout(request)


# --- admin user management ----------------------------------------------


@transaction.atomic
def set_user_flags(actor, target: User, *, is_host=None, is_admin=None, is_active=None) -> User:
    """Toggle host/admin/active on an account. Admin only, audited, self-safe."""
    if not can_manage_users(actor):
        raise ApiError("forbidden", "Only platform admins can change user flags.", 403)
    user = User.objects.select_for_update().get(pk=target.pk)
    if is_admin is False and user.pk == actor.pk:
        # The last admin must not be able to lock the portal out of its own admin.
        raise ApiError("cannot_demote_self",
                       "You cannot remove your own administrator flag.", 409)
    changes = {}
    updated = []
    for field, value in (("is_host", is_host), ("is_admin", is_admin), ("is_active", is_active)):
        if value is None or getattr(user, field) == value:
            continue
        changes[field] = {"from": getattr(user, field), "to": value}
        setattr(user, field, value)
        updated.append(field)
    if not updated:
        return user
    user.save(update_fields=updated)
    changed = ", ".join(f"{field} {value['from']} -> {value['to']}" for field, value in changes.items())
    audit.services.record(actor, "user.flags_changed", target=user,
                          summary=f"Changed {user.display_name or user.email}: {changed}.",
                          data=changes)
    return user


# --- API tokens ----------------------------------------------------------


@transaction.atomic
def create_api_token(user: User, name: str):
    """Issue a token. The plaintext is returned once and never stored."""
    name = (name or "").strip()
    if not name:
        raise ApiError("invalid", "Please correct the highlighted fields.", 400,
                       {"name": ["Give the token a name so you can recognise it later."]})
    if len(name) > 80:
        name = name[:80]
    token, plaintext = issue_token(user, name)
    audit.services.record(user, "api_token.created", target=token,
                          summary=f"Created the API token {name}.",
                          data={"prefix": token.prefix})
    return token, plaintext


@transaction.atomic
def revoke_api_token(user: User, prefix: str):
    """Revoke one of the caller's own tokens, addressed by its public prefix."""
    token = visible_tokens(user).filter(prefix=prefix).first()
    if token is None:
        raise ApiError("token_not_found", "No such token.", 404)
    if token.revoked_at is None:
        revoke_token(token)
        audit.services.record(user, "api_token.revoked", target=token,
                              summary=f"Revoked the API token {token.name}.",
                              data={"prefix": token.prefix})
    return token


# --- offline password reset ---------------------------------------------


@transaction.atomic
def create_reset_link(actor, target: User) -> tuple[PasswordResetToken, str]:
    """One-time reset link for an account: 24 hours, single use, stored hashed.

    Issuing a new link retires the outstanding ones, so an older link copied out
    of a chat window stops working the moment the user asks for a fresh one.
    """
    if not can_manage_users(actor):
        raise ApiError("forbidden", "Only platform admins can generate reset links.", 403)
    user = User.objects.select_for_update().get(pk=target.pk)
    now_ = now()
    PasswordResetToken.objects.filter(user=user, used_at__isnull=True).update(used_at=now_)
    plaintext = secrets.token_urlsafe(32)
    token = PasswordResetToken.objects.create(
        user=user,
        token_hash=hashlib.sha256(plaintext.encode("utf-8")).hexdigest(),
        expires_at=now_ + RESET_TOKEN_TTL,
        created_by=actor,
    )
    audit.services.record(actor, "user.reset_link_created", target=user,
                          summary=f"Generated a password reset link for {user.display_name or user.email}.",
                          data={"expires_at": token.expires_at.isoformat()})
    return token, plaintext


@transaction.atomic
def reset_password(token_value: str, password: str) -> User:
    """Consume a reset link and set a new password."""
    key_hash = hashlib.sha256((token_value or "").encode("utf-8")).hexdigest()
    token = PasswordResetToken.objects.select_for_update().filter(token_hash=key_hash).first()
    if token is None:
        raise ApiError("reset_token_invalid", "That reset link is not valid.", 400,
                       {"token": ["That reset link is not valid."]})
    if token.used_at is not None:
        raise ApiError("reset_token_used", "That reset link has already been used.", 400,
                       {"token": ["That reset link has already been used."]})
    if token.expires_at <= now():
        raise ApiError("reset_token_expired", "That reset link has expired.", 400,
                       {"token": ["That reset link has expired."]})
    user = User.objects.select_for_update().get(pk=token.user_id)
    if not user.is_active:
        raise ApiError("account_inactive", "That account is deactivated.", 400)
    errors = _password_errors(password)
    if errors:
        raise ApiError("invalid", "Please correct the highlighted fields.", 400, errors)
    user.set_password(password)
    user.save(update_fields=["password"])
    PasswordResetToken.objects.filter(pk=token.pk).update(used_at=now())
    _audit_password(user, "user.password_reset", "Password reset with an admin-issued link.")
    return user


@transaction.atomic
def change_password(user: User, current_password: str, password: str) -> User:
    """Change your own password: the current one is always required."""
    locked = User.objects.select_for_update().get(pk=user.pk)
    if not locked.has_usable_password() or not check_password(current_password or "", locked.password):
        raise ApiError("invalid_password", "Your current password is not correct.", 400,
                       {"current_password": ["Your current password is not correct."]})
    errors = _password_errors(password)
    if errors:
        raise ApiError("invalid", "Please correct the highlighted fields.", 400, errors)
    locked.set_password(password)
    locked.save(update_fields=["password"])
    _audit_password(locked, "user.password_changed", "Changed their own password.")
    return locked


# --- demo sign-in --------------------------------------------------------


@transaction.atomic
def demo_login(role: str) -> User:
    """The seeded account for a demo role.

    Raises 404 when the role is unknown or its account has not been seeded yet:
    a demo shortcut must never invent an account.
    """
    if not settings.DEMO_MODE:
        raise ApiError("demo_disabled", "Demo sign-in is not enabled on this portal.", 404)
    user = demo_account_for(role)
    if user is None:
        raise ApiError("demo_account_missing",
                       f"No demo account for role {role!r} is seeded on this portal.", 404)
    # A shortcut that skips the password is worth a line in the log.
    audit.services.record(user, "user.demo_login", target=user,
                          summary=f"Signed in as the demo {role} account.",
                          data={"role": role})
    return user
