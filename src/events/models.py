"""Events: the container every other record hangs off, plus its configuration."""
from django.conf import settings
from django.db import models

from core.ids import new_public_id


def _new_trk_id() -> str:
    return new_public_id("trk")


def _new_prz_id() -> str:
    return new_public_id("prz")


def _new_q_id() -> str:
    return new_public_id("q")


class JudgingMode(models.TextChoices):
    RUBRIC = "rubric", "Rubric"
    PAIRWISE = "pairwise", "Pairwise"
    BOTH = "both", "Both"


class RankingMethod(models.TextChoices):
    NORMALIZED = "normalized", "Normalized (judge offsets)"
    RAW = "raw", "Raw mean"
    PAIRWISE = "pairwise", "Pairwise (Bradley-Terry)"


class QuestionKind(models.TextChoices):
    SHORT_TEXT = "short_text", "Short text"
    LONG_TEXT = "long_text", "Long text"
    URL = "url", "URL"
    CHOICE = "choice", "Choice"
    YES_NO = "yes_no", "Yes / no"


class Role(models.TextChoices):
    PARTICIPANT = "participant", "Participant"
    JUDGE = "judge", "Judge"
    ORGANIZER = "organizer", "Organizer"


class PrizeScope(models.TextChoices):
    OVERALL = "overall", "Overall (any track)"
    TRACK = "track", "Track"


class Event(models.Model):
    """A hackathon. Slug is the public identifier; source_id keeps import provenance."""

    slug = models.SlugField(max_length=80, unique=True)
    source_id = models.CharField(max_length=40, null=True, blank=True, unique=True)
    name = models.CharField(max_length=160)
    tagline = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    submissions_open_at = models.DateTimeField(null=True, blank=True)
    submissions_close_at = models.DateTimeField()
    judging_open_at = models.DateTimeField(null=True, blank=True)
    judging_close_at = models.DateTimeField(null=True, blank=True)
    voting_open_at = models.DateTimeField(null=True, blank=True)
    voting_close_at = models.DateTimeField(null=True, blank=True)
    max_team_size = models.PositiveSmallIntegerField(default=4)
    reviews_per_project = models.PositiveSmallIntegerField(default=3)
    judging_mode = models.CharField(max_length=16, choices=JudgingMode.choices,
                                    default=JudgingMode.RUBRIC)
    ranking_method = models.CharField(max_length=16, choices=RankingMethod.choices,
                                      default=RankingMethod.NORMALIZED)
    shrinkage_lambda = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, default=None,
        help_text="Shrinkage penalty λ; null = auto (5-fold CV, BUILD-SPEC 19).",
    )
    scoring_locked_at = models.DateTimeField(null=True, blank=True)
    gallery_public = models.BooleanField(default=True)
    one_prize_per_team = models.BooleanField(
        default=True,
        help_text="A team already awarded a higher prize is skipped for lower ones.",
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="events_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "events_event"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(submissions_open_at__isnull=True)
                | models.Q(submissions_open_at__lt=models.F("submissions_close_at")),
                name="event_submissions_window_ordered",
            ),
            models.CheckConstraint(
                condition=models.Q(judging_open_at__isnull=True)
                | models.Q(judging_open_at__gte=models.F("submissions_close_at")),
                name="event_judging_after_submissions",
            ),
            models.CheckConstraint(
                condition=models.Q(judging_close_at__isnull=True)
                | models.Q(judging_open_at__isnull=True)
                | models.Q(judging_close_at__gt=models.F("judging_open_at")),
                name="event_judging_window_ordered",
            ),
            models.CheckConstraint(
                condition=models.Q(max_team_size__gte=1) & models.Q(max_team_size__lte=10),
                name="event_max_team_size_range",
            ),
            models.CheckConstraint(
                condition=models.Q(reviews_per_project__gte=1),
                name="event_reviews_per_project_positive",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def judging_open_time(self):
        """Judging never starts before submissions close (spec default)."""
        return self.judging_open_at or self.submissions_close_at


class Track(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="tracks")
    public_id = models.CharField(max_length=32, default=_new_trk_id)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    source_id = models.CharField(max_length=40, null=True, blank=True)

    class Meta:
        db_table = "events_track"
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["event", "name"], name="track_unique_name_per_event"),
            models.UniqueConstraint(fields=["event", "public_id"], name="track_unique_public_id_per_event"),
        ]

    def __str__(self) -> str:
        return f"{self.event.slug}: {self.name}"


class Prize(models.Model):
    """A prize and the place(s) it awards.

    ``scope`` decides the candidate pool (``results.prizes``): overall
    prizes draw from every ranked project, track prizes only from their
    track. A prize with a track is always a track prize, so the two
    cannot disagree.
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="prizes")
    public_id = models.CharField(max_length=32, default=_new_prz_id)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    value = models.CharField(max_length=120, blank=True)
    track = models.ForeignKey(Track, on_delete=models.CASCADE, null=True, blank=True,
                              related_name="prizes")
    position = models.PositiveSmallIntegerField(default=0)
    scope = models.CharField(
        max_length=8, choices=PrizeScope.choices, default=PrizeScope.OVERALL,
        help_text="Overall prizes draw from every ranked project; track prizes only from their track.",
    )
    places = models.PositiveSmallIntegerField(
        default=1,
        help_text="How many ranked places this prize awards (1 = single winner).",
    )
    eligibility_note = models.CharField(
        max_length=300, blank=True,
        help_text="Shown with the award, e.g. 'must ship a running demo'.",
    )

    class Meta:
        db_table = "events_prize"
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["event", "public_id"],
                                    name="prize_unique_public_id_per_event"),
            models.CheckConstraint(
                condition=models.Q(places__gte=1), name="prize_places_positive",
            ),
            models.CheckConstraint(
                condition=(models.Q(scope=PrizeScope.OVERALL, track__isnull=True)
                           | models.Q(scope=PrizeScope.TRACK, track__isnull=False)),
                name="prize_scope_matches_track",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event.slug}: {self.name}"

    def save(self, *args, **kwargs):
        """Keep scope and track in step: a track makes a prize a track prize.

        Doing it here (not only in the service) means bulk paths such as
        ``update_fields=["track"]`` cannot leave the row contradicting the
        ``prize_scope_matches_track`` constraint.
        """
        wanted = PrizeScope.TRACK if self.track_id else PrizeScope.OVERALL
        if self.scope != wanted:
            self.scope = wanted
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "scope" not in update_fields:
                kwargs["update_fields"] = [*update_fields, "scope"]
        super().save(*args, **kwargs)


class CustomQuestion(models.Model):
    """An extra question every project answers. is_public=False hides it from teams."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="questions")
    public_id = models.CharField(max_length=32, default=_new_q_id)
    prompt = models.CharField(max_length=300)
    help_text = models.CharField(max_length=300, blank=True)
    kind = models.CharField(max_length=16, choices=QuestionKind.choices,
                            default=QuestionKind.SHORT_TEXT)
    choices = models.JSONField(default=list, blank=True)
    required = models.BooleanField(default=False)
    is_public = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "events_custom_question"
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["event", "public_id"], name="question_unique_public_id_per_event"),
        ]


class EventRole(models.Model):
    """A user's role in one event. One row per (event, user) is the separation of duties."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="roles")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="event_roles")
    role = models.CharField(max_length=16, choices=Role.choices)
    public_id = models.CharField(max_length=32)
    tracks = models.ManyToManyField(Track, blank=True, related_name="judge_roles")
    source_id = models.CharField(max_length=40, null=True, blank=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name="roles_added")

    class Meta:
        db_table = "events_event_role"
        ordering = ["role", "id"]
        constraints = [
            models.UniqueConstraint(fields=["event", "user"], name="role_unique_per_event_user"),
            models.UniqueConstraint(fields=["event", "public_id"], name="role_unique_public_id_per_event"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.role} in {self.event_id}"

    def track_ids(self) -> set:
        return set(self.tracks.values_list("id", flat=True))
