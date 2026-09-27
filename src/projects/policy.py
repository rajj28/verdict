"""Projects, revisions, images and answers.

Read scoping and permission predicates. Every queryset in a view starts here.
"""
from django.db.models import QuerySet

from events.policy import is_organizer
from projects.models import Project, ProjectImage, ProjectStatus

PUBLIC_STATUSES = (ProjectStatus.SUBMITTED,)


def visible_projects() -> QuerySet[Project]:
    """The public gallery: submitted projects in events that publish a gallery.

    Drafts never appear here (BUILD-SEC section 5): withdrawn, disqualified and
    superseded rows are not "submitted" either, so they drop out of the same
    filter. Documented as intentionally public in THREAT-MODEL.md.
    """
    return Project.objects.filter(
        status__in=PUBLIC_STATUSES, event__gallery_public=True
    ).select_related("event", "team", "track")


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


def project_owning_media(name: str) -> Project | None:
    """The project that owns a stored media name (thumbnail or gallery image)."""
    project = Project.objects.filter(thumbnail=name).only("id", "event_id", "team_id",
                                                          "status").first()
    if project is not None:
        return project
    image = ProjectImage.objects.filter(image=name).only("project_id").first()
    return image.project if image is not None else None
