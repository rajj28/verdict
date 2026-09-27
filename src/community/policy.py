"""Read scopes and permission predicates for ballots, tallies and comments."""
from django.db.models import QuerySet

from audit.models import AuditEvent
from community.models import AbuseFlag, Ballot, BallotItem, Comment, VotingConfig
from core.clock import now
from core.errors import ApiError
from events.models import Event
from events.policy import is_organizer
from projects.models import Project


def visible_event(slug: str) -> Event | None:
    """Events may be browsed publicly; fetch only the requested event."""
    return Event.objects.filter(slug=slug).select_related("created_by").first()


def results_visible(user, event: Event) -> bool:
    """Results remain private until voting closes, even for published galleries."""
    if is_organizer(user, event):
        return True
    return (event.voting_close_at is not None and now() >= event.voting_close_at)


def can_manage_voting(user, event: Event) -> bool:
    """Whether this caller has organizer powers for the voting controls."""
    return is_organizer(user, event)


def require_visible_results(user, event: Event) -> None:
    if not results_visible(user, event):
        raise ApiError("results_hidden", "Voting results are hidden until voting closes.",
                       status_code=403)


def visible_tallies(user, event: Event) -> QuerySet[BallotItem]:
    """Count only active ballots after the result visibility policy allows access."""
    require_visible_results(user, event)
    return BallotItem.objects.filter(
        ballot__voter__event=event, ballot__voided_at__isnull=True,
        project__event=event, project__status="submitted",
    ).select_related("project", "ballot")


def visible_ballots(user, event: Event) -> QuerySet[Ballot]:
    """Private ballot details are available only to the event's organizers."""
    if not is_organizer(user, event):
        raise ApiError("forbidden", "Only organizers can inspect ballots.", status_code=403)
    return Ballot.objects.filter(voter__event=event).select_related(
        "voter", "voter__user"
    ).prefetch_related("items__project").order_by("created_at", "id")


def visible_flags(user, event: Event) -> QuerySet[AbuseFlag]:
    """Abuse details are private to the event organizers."""
    if not is_organizer(user, event):
        raise ApiError("forbidden", "Only organizers can inspect abuse flags.", status_code=403)
    return AbuseFlag.objects.filter(event=event).order_by("-created_at", "id")


def visible_config(user, event: Event) -> VotingConfig | None:
    """Voting rules and the open-link capability are organizer-only."""
    require_manager(user, event)
    return VotingConfig.objects.filter(event=event).first()


def visible_voting_audit(user, event: Event) -> QuerySet[AuditEvent]:
    """Voting identity and moderation history is organizer-only."""
    require_manager(user, event)
    return (AuditEvent.objects.filter(event=event, action__startswith="community.")
            .select_related("actor").order_by("-created_at", "-id"))


def visible_comments(user, project: Project) -> QuerySet[Comment]:
    """Public project comments exclude hidden rows and all private projects."""
    if project.status != "submitted" or not project.event.gallery_public:
        return Comment.objects.none()
    comments = Comment.objects.filter(project=project).select_related("author")
    if is_organizer(user, project.event):
        return comments.order_by("created_at", "id")
    return comments.filter(hidden_at__isnull=True).order_by("created_at", "id")


def require_manager(user, event: Event) -> None:
    if not getattr(user, "is_authenticated", False):
        raise ApiError("not_authenticated", "Authentication is required.", status_code=401)
    if not is_organizer(user, event):
        raise ApiError("forbidden", "Only organizers of this event can manage voting.",
                       status_code=403)
