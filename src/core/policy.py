"""Shared infrastructure: clock, ids, errors, csv, security headers.

Read scoping and permission predicates. Every queryset in a view starts here.
"""
from audit.models import AuditEvent
from django.db.models import QuerySet

from events.models import Event
from events.policy import is_organizer


def visible_probe_runs(user, event: Event | None = None) -> QuerySet[AuditEvent]:
    """Probe reports are visible to platform admins or the organizer of their event."""
    if user is None or not getattr(user, "is_authenticated", False):
        return AuditEvent.objects.none()
    if event is None:
        if not getattr(user, "is_admin", False):
            return AuditEvent.objects.none()
        rows = AuditEvent.objects.filter(event__isnull=True)
    else:
        if not is_organizer(user, event):
            return AuditEvent.objects.none()
        rows = AuditEvent.objects.filter(event=event)
    return rows.filter(action="integrity.probe_run").order_by("-created_at", "-id")
