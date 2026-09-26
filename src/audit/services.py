"""The only way to write an AuditEvent. Called inside the same transaction as the change."""
import hashlib

from django.conf import settings
from django.db import models

from audit.models import AuditEvent


def hash_ip(ip: str | None) -> str | None:
    """Store a pseudonymous ip, never the address itself."""
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8") + settings.SECRET_KEY.encode("utf-8")).hexdigest()[:16]


def _client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _actor_label(actor, event=None) -> str:
    """Snapshot like "Priya (participant)" so the log survives later renames."""
    if actor is None:
        return "system"
    name = actor.display_name or actor.email.split("@")[0]
    if event is not None and getattr(actor, "is_authenticated", False):
        role_row = actor.event_roles.filter(event=event).only("role").first()
        if role_row is not None:
            return f"{name} ({role_row.get_role_display().lower()})"
    return name


def _target_reference(target) -> tuple[str, str]:
    """Return (target_type, target_id) from any model instance with a public id."""
    if target is None:
        return "", ""
    if isinstance(target, models.Model):
        public_id = getattr(target, "public_id", None) or getattr(target, "slug", None)
        return target._meta.label_lower, str(public_id or "")
    return type(target).__name__, str(target)


def record(actor, action: str, *, event=None, target=None, summary: str, data: dict | None = None,
           request=None) -> AuditEvent:
    """Write one audit row inside the caller's transaction."""
    target_type, target_id = _target_reference(target)
    return AuditEvent.objects.create(
        event=event,
        actor=actor if getattr(actor, "pk", None) else None,
        actor_label=_actor_label(actor, event),
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=summary[:300],
        data=data or {},
        ip_hash=hash_ip(_client_ip(request)),
    )
