"""Audit: read scoping and permission predicates.

The audit log is append-only; no writes happen here, only scoped reads.
"""
from __future__ import annotations

from django.db.models import QuerySet
from django.utils.dateparse import parse_datetime

from audit.models import AuditEvent
from core.errors import ApiError
from events.models import Event
from events.policy import is_organizer


def require_organizer(user, event: Event) -> None:
    """Raise 401/403 unless the caller is authenticated and an organizer/admin."""
    if user is None or not getattr(user, "is_authenticated", False):
        raise ApiError("not_authenticated", "Authentication is required.", status_code=401)
    if not is_organizer(user, event):
        raise ApiError(
            "forbidden",
            "Only organizers of this event may read the audit log.",
            status_code=403,
        )


def visible_audit_events(
    user,
    event: Event,
    action: str | None = None,
    actor_public_id: str | None = None,
    since: str | None = None,
) -> QuerySet[AuditEvent]:
    """All audit events for an event, optionally filtered.

    Organizers and admins only (enforced before this call via require_organizer).
    Filters:
      - action: prefix match (e.g. "project" matches all "project.*" actions)
      - actor_public_id: filter by the actor's user public_id
      - since: ISO 8601 datetime string
    """
    qs = (
        AuditEvent.objects.filter(event=event)
        .select_related("actor")
        .order_by("-created_at", "-id")
    )
    if action:
        qs = qs.filter(action__startswith=action)
    if actor_public_id:
        qs = qs.filter(actor__public_id=actor_public_id)
    if since:
        since_dt = parse_datetime(since)
        if since_dt is not None:
            qs = qs.filter(created_at__gte=since_dt)
    return qs
