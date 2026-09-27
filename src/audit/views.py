"""Append-only audit log.

Server-rendered, read-only view. Every write goes through the JSON API.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from audit.policy import visible_audit_events
from events.models import Event, EventRole, Role
from events.policy import can_manage, visible_events

PAGE_SIZE = 50  # the same page size the JSON audit API uses, so both agree

#: The action prefixes an organizer filters by, in the order they occur in a run.
ACTION_GROUPS = [
    ("", "Everything"),
    ("project", "Projects and submissions"),
    ("team", "Teams and members"),
    ("judging", "Rubric, judges, assignments"),
    ("review", "Reviews and exclusions"),
    ("results", "Results and publication"),
    ("event", "Event windows and settings"),
    ("import", "Imports"),
]

#: Roles that appear as a pill next to the actor, so a reader can tell at a glance
#: who was allowed to do what.
PILL_ROLES = (Role.ORGANIZER, Role.JUDGE, Role.PARTICIPANT)


def _managed_event(request, slug: str) -> Event:
    """The event this log belongs to, or a refusal (events.policy.can_manage)."""
    event = get_object_or_404(visible_events(), slug=slug)
    if not can_manage(request.user, event):
        raise PermissionDenied("Only organizers of this event can open this page.")
    return event


def _role_map(event: Event) -> dict[int, str]:
    """user pk -> role in this event, in one query.

    The log shows one row per event change, so resolving the role row by row
    would be the one N+1 on this page.
    """
    return dict(
        EventRole.objects.filter(event=event, role__in=PILL_ROLES)
        .values_list("user_id", "role")
    )


@login_required
def audit_log(request, slug: str):
    """``/manage/{slug}/audit``: a readable, filterable log with a CSV link.

    The filters are the query parameters of the audit API (action prefix, actor,
    since) applied through audit.policy.visible_audit_events, so a filtered log
    is a link an organizer can share with a co-organizer.
    """
    event = _managed_event(request, slug)
    action = (request.GET.get("action") or "").strip()
    actor = (request.GET.get("actor") or "").strip()
    since = (request.GET.get("since") or "").strip()
    rows = visible_audit_events(
        request.user, event, action=action or None, actor_public_id=actor or None,
        since=since or None,
    )
    page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))
    role_map = _role_map(event)
    entries = [
        {"event": row, "role": role_map.get(row.actor_id)}
        for row in page.object_list
    ]
    actors = [
        {"public_id": role.user.public_id, "name": role.user.display_name
         or role.user.email.split("@")[0]}
        for role in EventRole.objects.filter(event=event, role__in=PILL_ROLES)
        .select_related("user")
        .order_by("public_id")
    ]
    return render(request, "manage/audit.html", {
        "event": event,
        "entries": entries,
        "page_obj": page,
        "result_count": page.paginator.count,
        "actors": actors,
        "action": action,
        "actor": actor,
        "since": since,
        "action_groups": ACTION_GROUPS,
        "export_url": f"/api/v1/events/{event.slug}/exports/audit.csv",
    })
