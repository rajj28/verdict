"""Projects, immutable revisions on submit, images and custom-question answers."""
from django.conf import settings
from django.db import models

from core.ids import new_public_id


def _new_prj_id() -> str:
    return new_public_id("prj")
ACTIVE_STATUSES = ("draft", "submitted")


class ProjectStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    WITHDRAWN = "withdrawn", "Withdrawn"
    DISQUALIFIED = "disqualified", "Disqualified"
    SUPERSEDED = "superseded", "Superseded"


class Project(models.Model):
    """A team's entry. Only one project per team may be draft or submitted at a time."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="projects")
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, related_name="projects")
    track = models.ForeignKey("events.Track", on_delete=models.PROTECT, null=True, blank=True,
                              related_name="projects")
    public_id = models.CharField(max_length=32, default=_new_prj_id)
    source_id = models.CharField(max_length=40, null=True, blank=True)
    title = models.CharField(max_length=120)
    summary = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    thumbnail = models.ImageField(upload_to="thumbnails", null=True, blank=True)
    demo_video_url = models.URLField(max_length=300, blank=True)
    repo_url = models.URLField(max_length=300, blank=True)
    live_url = models.URLField(max_length=300, blank=True)
    tech_tags = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=ProjectStatus.choices,
                              default=ProjectStatus.DRAFT)
    first_submitted_at = models.DateTimeField(null=True, blank=True)
    last_submitted_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=0)
    superseded_by = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="superseded_projects")
    status_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "projects_project"
        ordering = ["title", "id"]
        constraints = [
            models.UniqueConstraint(fields=["event", "public_id"], name="project_unique_public_id_per_event"),
            models.UniqueConstraint(
                fields=["team"],
                condition=models.Q(status__in=["draft", "submitted"]),
                name="project_one_active_per_team",
            ),
        ]
        indexes = [
            models.Index(fields=["event", "status"], name="project_event_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.public_id} {self.title}"

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def snapshot(self) -> dict:
        """Canonical public+private field set used for revision snapshots and digests."""
        return {
            "public_id": self.public_id,
            "title": self.title,
            "summary": self.summary,
            "description": self.description,
            "track": self.track.public_id if self.track_id else None,
            "team": self.team.public_id,
            "demo_video_url": self.demo_video_url,
            "repo_url": self.repo_url,
            "live_url": self.live_url,
            "tech_tags": list(self.tech_tags or []),
            "thumbnail": self.thumbnail.name if self.thumbnail else None,
        }


class ProjectRevision(models.Model):
    """One immutable snapshot per submit, and per edit while submitted."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="revisions")
    number = models.PositiveIntegerField()
    snapshot = models.JSONField(default=dict)
    digest = models.CharField(max_length=64, blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="project_revisions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "projects_project_revision"
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="revision_unique_per_project"),
        ]

    def __str__(self) -> str:
        return f"{self.project_id} revision {self.number}"


class ProjectImage(models.Model):
    """Gallery image. Uploads are capped at 6 per project by the service."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="projects")
    caption = models.CharField(max_length=200, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "projects_project_image"
        ordering = ["position", "id"]


class Answer(models.Model):
    """A project's answer to one custom question."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey("events.CustomQuestion", on_delete=models.CASCADE,
                                 related_name="answers")
    value = models.TextField(max_length=2000, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "projects_answer"
        ordering = ["question_id"]
        constraints = [
            models.UniqueConstraint(fields=["project", "question"], name="answer_unique_per_project"),
        ]

    def __str__(self) -> str:
        return f"{self.project_id} -> {self.question_id}"
