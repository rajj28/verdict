"""Rubric, judges, conflicts, assignments, reviews, scores and pairwise comparisons."""
import secrets

from django.conf import settings
from django.db import models

from core.ids import new_public_id
from core.clock import now


def _new_asg_id() -> str:
    return new_public_id("asg")


def _new_rev_id() -> str:
    return new_public_id("rev")


def _new_cmp_id() -> str:
    return new_public_id("cmp")
TOKEN_BYTES = 32  # secrets.token_urlsafe(32) -> 43 url-safe characters

def new_judge_invite_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


class ReviewStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"


class ReviewSource(models.TextChoices):
    LIVE = "live", "Live"
    IMPORT = "import", "Import"


class ConflictSource(models.TextChoices):
    DECLARED_BY_JUDGE = "declared_by_judge", "Declared by judge"
    ORGANIZER = "organizer", "Organizer"


class AssignmentMethod(models.TextChoices):
    MANUAL = "manual", "Manual"
    AUTO = "auto", "Automatic"
    IMPORT = "import", "Import"
    REBALANCE = "rebalance", "Rebalance"


class Rubric(models.Model):
    """One rubric per event; the version travels with every review."""

    event = models.OneToOneField("events.Event", on_delete=models.CASCADE, related_name="rubric")
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "judging_rubric"

    def __str__(self) -> str:
        return f"{self.event_id} rubric v{self.version}"


class Criterion(models.Model):
    """One weighted, bounded scoring criterion."""

    rubric = models.ForeignKey(Rubric, on_delete=models.CASCADE, related_name="criteria")
    key = models.SlugField(max_length=60)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    weight = models.DecimalField(max_digits=6, decimal_places=3)
    min_score = models.PositiveSmallIntegerField(default=1)
    max_score = models.PositiveSmallIntegerField(default=5)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "judging_criterion"
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["rubric", "key"], name="criterion_unique_key_per_rubric"),
            models.CheckConstraint(condition=models.Q(weight__gt=0), name="criterion_weight_positive"),
            models.CheckConstraint(condition=models.Q(max_score__gt=models.F("min_score")),
                                   name="criterion_max_above_min"),
        ]

    def __str__(self) -> str:
        return f"{self.key} ({self.min_score}-{self.max_score})"

    def clamp(self, value: int) -> int:
        return max(self.min_score, min(self.max_score, int(value)))


class JudgeInvite(models.Model):
    """An emailed invitation. Accepting requires the account email to match."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="judge_invites")
    email = models.EmailField()
    tracks = models.ManyToManyField("events.Track", blank=True, related_name="judge_invites")
    token = models.CharField(max_length=64, unique=True, default=new_judge_invite_token)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="judge_invites_created")
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name="judge_invites_accepted")
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "judging_judge_invite"
        ordering = ["-created_at"]


class Conflict(models.Model):
    """A declared conflict of interest between a judge and a team."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="conflicts")
    judge = models.ForeignKey("events.EventRole", on_delete=models.CASCADE, related_name="conflicts")
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, related_name="conflicts")
    reason = models.CharField(max_length=300, blank=True)
    source = models.CharField(max_length=20, choices=ConflictSource.choices,
                              default=ConflictSource.ORGANIZER)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="conflicts_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "judging_conflict"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["judge", "team"], name="conflict_unique_judge_team"),
        ]


class AssignmentBatch(models.Model):
    """One run of the assignment algorithm, kept for the audit trail."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="assignment_batches")
    method = models.CharField(max_length=16, choices=AssignmentMethod.choices)
    params = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="assignment_batches_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "judging_assignment_batch"
        ordering = ["-created_at"]


class Assignment(models.Model):
    """A judge is assigned to review one project."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="assignments")
    judge = models.ForeignKey("events.EventRole", on_delete=models.CASCADE, related_name="assignments")
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="assignments")
    batch = models.ForeignKey(AssignmentBatch, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="assignments")
    public_id = models.CharField(max_length=32, default=_new_asg_id)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "judging_assignment"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["judge", "project"], name="assignment_unique_judge_project"),
            models.UniqueConstraint(fields=["event", "public_id"], name="assignment_unique_public_id_per_event"),
        ]

    def __str__(self) -> str:
        return f"{self.judge_id} -> {self.project_id}"


class Review(models.Model):
    """One review of one project by one judge. Comments stay private to judge+organizers."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="reviews")
    assignment = models.OneToOneField(Assignment, on_delete=models.CASCADE, related_name="review")
    judge = models.ForeignKey("events.EventRole", on_delete=models.CASCADE, related_name="reviews")
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="reviews")
    public_id = models.CharField(max_length=32, default=_new_rev_id)
    status = models.CharField(max_length=16, choices=ReviewStatus.choices, default=ReviewStatus.DRAFT)
    comment = models.TextField(max_length=2000, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    rubric_version = models.PositiveIntegerField(default=1)
    project_revision = models.PositiveIntegerField(default=1)
    source = models.CharField(max_length=16, choices=ReviewSource.choices, default=ReviewSource.LIVE)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "judging_review"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["event", "public_id"],
                                    name="review_unique_public_id_per_event"),
        ]

    def __str__(self) -> str:
        return f"{self.public_id} {self.judge_id}/{self.project_id}"

    @property
    def is_submitted(self) -> bool:
        return self.status == ReviewStatus.SUBMITTED


class CriterionScore(models.Model):
    """A single criterion value inside a review, bounded by the criterion."""

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="scores")
    criterion = models.ForeignKey(Criterion, on_delete=models.CASCADE, related_name="scores")
    value = models.SmallIntegerField()

    class Meta:
        db_table = "judging_criterion_score"
        ordering = ["criterion_id"]
        constraints = [
            models.UniqueConstraint(fields=["review", "criterion"], name="score_unique_per_review"),
        ]

    def __str__(self) -> str:
        return f"{self.review_id} {self.criterion_id}={self.value}"


class ReviewExclusion(models.Model):
    """An organizer's decision to leave a review out of results. Never deletes it."""

    review = models.OneToOneField(Review, on_delete=models.CASCADE, related_name="exclusion")
    reason = models.CharField(max_length=300)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="review_exclusions_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "judging_review_exclusion"


class Comparison(models.Model):
    """A pairwise verdict. winner=None means the judge skipped the pair."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="comparisons")
    public_id = models.CharField(max_length=32, unique=True, default=_new_cmp_id)
    judge = models.ForeignKey("events.EventRole", on_delete=models.CASCADE, related_name="comparisons")
    left = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="comparisons_left")
    right = models.ForeignKey("projects.Project", on_delete=models.CASCADE,
                              related_name="comparisons_right")
    winner = models.ForeignKey("projects.Project", on_delete=models.CASCADE, null=True, blank=True,
                               related_name="comparisons_won")
    created_at = models.DateTimeField(default=now, editable=False)
    retracted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "judging_comparison"
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(left=models.F("right")),
                                   name="comparison_distinct_projects"),
            models.CheckConstraint(condition=models.Q(left__lt=models.F("right")),
                                   name="comparison_canonical_pair"),
            models.CheckConstraint(condition=models.Q(winner__isnull=True)
                                   | models.Q(winner=models.F("left"))
                                   | models.Q(winner=models.F("right")),
                                   name="comparison_winner_in_pair"),
            models.UniqueConstraint(fields=["judge", "left", "right"],
                                    condition=models.Q(retracted_at__isnull=True),
                                    name="comparison_unique_active_pair"),
        ]
