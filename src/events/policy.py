"""Events, tracks, prizes, custom questions and per-event roles.

Read scoping and permission predicates. Every queryset in a view starts here.
"""
from django.db.models import Q, QuerySet

from core.clock import now
from events.models import Event, EventRole, Role

ADMIN = "admin"


def role_of(user, event: Event) -> str | None:
    """The user's role in this event: participant, judge, organizer or admin.

    Admins hold organizer powers everywhere, so they read as "admin" (BUILD-SEC
    section 3). One role per person per event is a DB constraint, so this can
    never be ambiguous.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if user.is_admin:
        return ADMIN
    row = role_row(user, event)
    return row.role if row is not None else None


def role_row(user, event: Event) -> EventRole | None:
    """The caller's EventRole in this event, or None."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return EventRole.objects.filter(event=event, user=user).first()


def is_organizer(user, event: Event) -> bool:
    return role_of(user, event) in (Role.ORGANIZER, ADMIN)


def is_participant(user, event: Event) -> bool:
    return role_of(user, event) == Role.PARTICIPANT


def judge_role(user, event: Event) -> EventRole | None:
    """The caller's judge role in this event, or None if they are not a judge here."""
    row = role_row(user, event)
    return row if row is not None and row.role == Role.JUDGE else None


def judge_roles(user) -> QuerySet[EventRole]:
    """Every judge role the caller holds, across all events."""
    if user is None or not getattr(user, "is_authenticated", False):
        return EventRole.objects.none()
    return (EventRole.objects.filter(user=user, role=Role.JUDGE)
            .select_related("event", "user").prefetch_related("tracks"))


def is_judge(user) -> bool:
    return judge_roles(user).exists()


def submission_window_open(event: Event, at=None) -> bool:
    """Half-open [submissions_open_at, submissions_close_at); a null bound is open."""
    at = at or now()
    if event.submissions_open_at is not None and at < event.submissions_open_at:
        return False
    return at < event.submissions_close_at


def judging_window_open(event: Event, at=None) -> bool:
    """Half-open [judging_open_at, judging_close_at); a null close means still open.

    Judging never starts before submissions close, so a null open bound falls
    back to the submissions close time.
    """
    at = at or now()
    opens_at = event.judging_open_time()
    if opens_at is not None and at < opens_at:
        return False
    return event.judging_close_at is None or at < event.judging_close_at


def visible_events(user=None) -> QuerySet[Event]:
    """Events are browseable by every role and visitor."""
    sandbox = Q(source_id__startswith="evt_tour_")
    if user is not None and getattr(user, "is_authenticated", False):
        return Event.objects.filter(~sandbox | Q(roles__user=user)).distinct()
    return Event.objects.filter(~sandbox)


def can_manage(user, event: Event) -> bool:
    return is_organizer(user, event)


def get_event_by_slug(slug: str) -> Event | None:
    return Event.objects.filter(slug=slug).first()
