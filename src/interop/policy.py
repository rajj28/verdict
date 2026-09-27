"""Read scoping and permission predicates for interop features."""
from django.db.models import Count, Prefetch, Q, QuerySet

from events.models import Event, EventRole, Role
from judging.models import Review, ReviewStatus
from interop.models import JudgeParticipationRecord, WebhookDelivery, WebhookEndpoint
from projects.models import Project, ProjectStatus
from results.models import ResultPublication
from teams.models import Team, TeamMember


def _certificate_project_queryset(queryset: QuerySet[Project]) -> QuerySet[Project]:
    members = TeamMember.objects.select_related("user").order_by("joined_at", "id")
    return queryset.select_related("team", "track").prefetch_related(
        Prefetch("team__memberships", queryset=members)
    )


def visible_endpoints(user, event: Event) -> QuerySet[WebhookEndpoint]:
    from events.policy import is_organizer

    if not is_organizer(user, event):
        return WebhookEndpoint.objects.none()
    return WebhookEndpoint.objects.filter(event=event).order_by("-created_at")


def visible_deliveries(user, event: Event) -> QuerySet[WebhookDelivery]:
    from events.policy import is_organizer

    if not is_organizer(user, event):
        return WebhookDelivery.objects.none()
    return WebhookDelivery.objects.filter(event=event).select_related("endpoint").order_by("-created_at")


def visible_records(user=None, event: Event | None = None) -> QuerySet[JudgeParticipationRecord]:
    rows = JudgeParticipationRecord.objects.select_related("event", "judge__user")
    if event is not None:
        rows = rows.filter(event=event)
    return rows


def certificate_projects(event: Event) -> QuerySet[Project]:
    return _certificate_project_queryset(
        Project.objects.filter(event=event, status=ProjectStatus.SUBMITTED)
    )


def certificate_award_projects(event: Event) -> QuerySet[Project]:
    return _certificate_project_queryset(Project.objects.filter(event=event))


def certificate_project(event: Event, public_id: str, *, submitted_only: bool = True) -> QuerySet[Project]:
    if submitted_only:
        projects = certificate_projects(event)
    else:
        projects = certificate_award_projects(event)
    return projects.filter(public_id=public_id)


def certificate_judges(event: Event) -> QuerySet[EventRole]:
    return event.roles.filter(role=Role.JUDGE).select_related("user").annotate(
        submitted_reviews=Count(
            "reviews",
            filter=Q(reviews__event=event, reviews__status=ReviewStatus.SUBMITTED),
        )
    )


def certificate_judge(event: Event, public_id: str) -> QuerySet[EventRole]:
    return certificate_judges(event).filter(public_id=public_id)


def certificate_reviews(event: Event, judge: EventRole) -> QuerySet[Review]:
    return Review.objects.filter(event=event, judge=judge, status=ReviewStatus.SUBMITTED)


def certificate_members(event: Event, team: Team) -> QuerySet[TeamMember]:
    return (
        TeamMember.objects.filter(event=event, team=team)
        .select_related("user")
        .order_by("joined_at", "id")
    )


def certificate_publications(event: Event) -> QuerySet[ResultPublication]:
    return ResultPublication.objects.filter(event=event).order_by("-published_at")
