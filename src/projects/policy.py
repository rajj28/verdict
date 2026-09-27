"""Projects, revisions, images and answers.

Read scoping and permission predicates. Every queryset in a view starts here.
"""
from django.db import connection
from django.db.models import Q, QuerySet

from events.policy import is_organizer
from projects.models import Project, ProjectImage, ProjectStatus

PUBLIC_STATUSES = (ProjectStatus.SUBMITTED,)
SORT_TITLE = "title"
SORT_NEWEST = "newest"
SORTS = (SORT_TITLE, SORT_NEWEST)


def public_projects(event=None) -> QuerySet[Project]:
    """The public gallery: submitted projects in events that publish a gallery.

    Drafts never appear here (BUILD-SEC section 5): withdrawn, disqualified and
    superseded rows are not "submitted" either, so they drop out of the same
    filter. Documented as intentionally public in THREAT-MODEL.md.
    """
    projects = Project.objects.filter(
        status__in=PUBLIC_STATUSES, event__gallery_public=True
    ).select_related("event", "team", "track")
    if event is not None:
        projects = projects.filter(event=event)
    return projects


def _filter_tag(queryset: QuerySet[Project], tag: str) -> QuerySet[Project]:
    """Match one whole tag inside the JSON list, on either supported backend.

    Postgres has @> for jsonb containment; SQLite has no contains lookup on a
    JSONField, so the same question is asked with json_each. A substring match
    would put "go" next to every project tagged "django", which is worse than
    useless in a filter.
    """
    if connection.vendor == "postgresql":
        return queryset.filter(tech_tags__contains=[tag])
    return queryset.extra(
        where=["EXISTS (SELECT 1 FROM json_each(tech_tags) WHERE json_each.value = %s)"],
        params=[tag],
    )


def filter_gallery(projects: QuerySet[Project], *, q: str = "", track: str = "",
                   tag: str = "", sort: str = SORT_TITLE) -> QuerySet[Project]:
    """Search and filter the gallery queryset.

    One queryset, four optional filters: the page never runs a query per card, and
    the count the paginator needs is the same query the page slice comes from.
    """
    query = (q or "").strip()
    if query:
        projects = projects.filter(
            Q(title__icontains=query)
            | Q(summary__icontains=query)
            | Q(description__icontains=query)
        )
    if track:
        projects = projects.filter(track__public_id=track)
    if tag:
        # Tags are stored lowercased and matched exactly (see _filter_tag).
        projects = _filter_tag(projects, tag.strip().lower())
    if sort == SORT_NEWEST:
        return projects.order_by("-last_submitted_at", "title", "id")
    return projects.order_by("title", "id")


def gallery_facets(event=None) -> tuple[list[dict], list[tuple[str, str]]]:
    """The event and track filter options, from one query.

    A public project already implies a public event and a track, so the filter
    lists are derived from the same rows the gallery shows. That keeps the page at
    a constant three queries: facets, count, page.
    """
    rows = (
        public_projects(event)
        .values_list("event__slug", "event__name", "track__public_id", "track__name")
        .distinct()
    )
    events: dict[str, str] = {}
    tracks: dict[str, str] = {}
    for event_slug, event_name, track_public_id, track_name in rows:
        events.setdefault(event_slug, event_name)
        if track_public_id:
            tracks.setdefault(track_public_id, track_name)
    return (
        [{"slug": slug, "name": name} for slug, name in sorted(events.items(), key=lambda row: row[1])],
        sorted(tracks.items(), key=lambda row: (row[1], row[0])),
    )


# The name the home page and the results pages already import; the gallery
# queryset is the same object either way.
visible_projects = public_projects


def can_see_project(user, project: Project) -> bool:
    """Whether this caller may read this one project, drafts included."""
    if project.status in PUBLIC_STATUSES and project.event.gallery_public:
        return True
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if is_organizer(user, project.event):
        return True
    if project.team.memberships.filter(user=user).exists():
        return True
    return project.assignments.filter(judge__user=user).exists()


def visible_project(user, project: Project | None) -> Project | None:
    """The project if the caller may read it, else None (callers answer 404)."""
    if project is None:
        return None
    return project if can_see_project(user, project) else None


def visible_project_by_id(user, event, public_id: str) -> Project | None:
    """One project of one event by public_id, or None when the caller may not see it.

    Ids are resolved inside the event and then filtered through the same rule as
    the list, so a draft answers 404 for the public exactly like a missing row.
    """
    if not public_id or event is None:
        return None
    project = (
        Project.objects.filter(event=event, public_id=public_id)
        .select_related("event", "team", "track")
        .first()
    )
    return visible_project(user, project)


def can_edit_project(user, project: Project) -> bool:
    """Whether this caller may write the project (team members, active statuses)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not project.is_active:
        return False
    return project.team.memberships.filter(user=user).exists()


def can_see_private_answers(user, project: Project) -> bool:
    """Custom answers with is_public=False: the team, organizers, assigned judges.

    Judges reach it only through an assignment, which is also what limits them to
    projects in their own tracks (BUILD-SEC section 3).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if is_organizer(user, project.event):
        return True
    if project.team.memberships.filter(user=user).exists():
        return True
    return project.assignments.filter(judge__user=user).exists()


def visible_answers(user, project: Project) -> QuerySet:
    """The answers this caller may read, with the question joined in."""
    answers = project.answers.select_related("question")
    if can_see_private_answers(user, project):
        return answers
    return answers.filter(question__is_public=True)


def visible_images(project: Project) -> QuerySet[ProjectImage]:
    """Gallery images in display order (one query)."""
    return project.images.all().order_by("position", "id")


def project_owning_media(name: str) -> Project | None:
    """The project that owns a stored media name (thumbnail or gallery image)."""
    project = Project.objects.filter(thumbnail=name).only("id", "event_id", "team_id",
                                                          "status").first()
    if project is not None:
        return project
    image = ProjectImage.objects.filter(image=name).only("project_id").first()
    return image.project if image is not None else None
