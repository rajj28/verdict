"""Results: read scoping and permission predicates.

All queryset entry points for results live here so views and API classes
never call the ORM directly.
"""
from __future__ import annotations

from django.db.models import QuerySet

from core.errors import ApiError
from events.models import Event
from events.policy import is_organizer
from results.models import ResultPublication


def require_organizer(user, event: Event) -> None:
    """Raise 401/403 unless the caller is an authenticated organizer/admin."""
    if user is None or not getattr(user, "is_authenticated", False):
        raise ApiError("not_authenticated", "Authentication is required.", status_code=401)
    if not is_organizer(user, event):
        raise ApiError(
            "forbidden",
            "Only organizers of this event can manage results.",
            status_code=403,
        )


def visible_publications(user, event: Event) -> QuerySet[ResultPublication]:
    """Organizers see all; public users see the latest only when published."""
    require_organizer(user, event)
    return (
        ResultPublication.objects.filter(event=event)
        .select_related("published_by", "supersedes")
        .order_by("-version")
    )


def latest_publication(event: Event) -> ResultPublication | None:
    """The most recent (non-superseded) publication, or None."""
    return (
        ResultPublication.objects.filter(event=event)
        .select_related("supersedes")
        .order_by("-version")
        .first()
    )


def public_publications(event: Event) -> QuerySet[ResultPublication]:
    """Only the version, timestamp, note and digest are exposed as history."""
    return ResultPublication.objects.filter(event=event).order_by("-version")


def public_results_available(event: Event) -> bool:
    """True when at least one publication exists for this event."""
    return ResultPublication.objects.filter(event=event).exists()


def get_publication(event: Event, pub_public_id: str) -> ResultPublication:
    """Fetch a publication by public_id inside an event, or raise 404."""
    pub = ResultPublication.objects.filter(
        event=event, public_id=pub_public_id
    ).select_related("published_by", "supersedes", "event").first()
    if pub is None:
        raise ApiError(
            "publication_not_found",
            f"No publication {pub_public_id!r} found for this event.",
            status_code=404,
        )
    return pub


def can_see_feedback(user, event: Event, project) -> bool:
    """A team member of the project may see feedback after it is released."""
    from events.models import EventRole, Role
    from teams.models import TeamMember

    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if is_organizer(user, event):
        return True
    # Must be a team member of the project's team.
    return TeamMember.objects.filter(
        team=project.team, user=user
    ).exists()
