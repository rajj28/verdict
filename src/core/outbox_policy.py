"""Outbox bodies contain capabilities: only owning organizers and admins read them."""
from django.db.models import QuerySet

from core.models import OutboxMessage
from events.models import Event, Role
from events.policy import is_organizer


def can_view_outbox(user, event: Event | None = None) -> bool:
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    return bool(user.is_admin or (event is not None and is_organizer(user, event)))


def visible_outbox(user, event: Event | None = None) -> QuerySet[OutboxMessage]:
    rows = OutboxMessage.objects.select_related("event")
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return rows.none()
    if event is not None:
        rows = rows.filter(event=event)
    if user.is_admin:
        return rows
    if event is None:
        return rows.none()
    return rows.filter(event__roles__user=user, event__roles__role=Role.ORGANIZER)
