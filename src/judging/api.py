"""Rubric, judges, assignments, reviews and scores.

DRF serializers and viewsets. Thin: validate, delegate to services, return the envelope.
"""
from core.errors import ApiError
from drf_spectacular.utils import OpenApiParameter, extend_schema
from events.models import Event, EventRole, Role
from events.policy import get_event_by_slug, is_organizer, judge_roles
from judging import policy, services
from judging.models import Assignment, Criterion, Review
from judging.policy import (judge_reviews, review_values, reviews_for_judge_role,
                            scored_reviews, visible_reviews)
from projects.models import Project
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.schema import error_responses

TAGS = ["judging"]


class ProjectRefSerializer(serializers.Serializer):
    """The project as a score listing shows it: public id and title, nothing else."""

    public_id = serializers.CharField()
    title = serializers.CharField()


class JudgeSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    event = serializers.CharField()
    name = serializers.CharField()
    tracks = serializers.ListField(child=serializers.CharField())


class ReviewSerializer(serializers.Serializer):
    review_id = serializers.CharField()
    event = serializers.CharField()
    project = ProjectRefSerializer()
    status = serializers.CharField()
    criteria = serializers.DictField(child=serializers.IntegerField())
    weighted_score = serializers.FloatField()
    comment = serializers.CharField(allow_null=True)
    submitted_at = serializers.DateTimeField(allow_null=True)


class JudgeScoresSerializer(serializers.Serializer):
    judge = JudgeSerializer(allow_null=True)
    judges = JudgeSerializer(many=True)
    reviews = ReviewSerializer(many=True)


class CriterionWriteSerializer(serializers.Serializer):
    key = serializers.SlugField(max_length=60)
    name = serializers.CharField(max_length=120)
    description = serializers.CharField(required=False, allow_blank=True)
    weight = serializers.DecimalField(max_digits=6, decimal_places=3)
    min_score = serializers.IntegerField(required=False, min_value=0, max_value=32767)
    max_score = serializers.IntegerField(required=False, min_value=1, max_value=32767)
    position = serializers.IntegerField(required=False, min_value=0)


class RubricWriteSerializer(serializers.Serializer):
    criteria = CriterionWriteSerializer(many=True, min_length=1, max_length=10)


class JudgeWriteSerializer(serializers.Serializer):
    email = serializers.EmailField()
    tracks = serializers.ListField(child=serializers.CharField(), required=False)


class TracksWriteSerializer(serializers.Serializer):
    tracks = serializers.ListField(child=serializers.CharField())


class InviteWriteSerializer(serializers.Serializer):
    emails = serializers.ListField(child=serializers.EmailField(), min_length=1)
    tracks = serializers.ListField(child=serializers.CharField(), required=False)


class ConflictWriteSerializer(serializers.Serializer):
    judge = serializers.CharField(required=False)
    team = serializers.CharField()
    reason = serializers.CharField(required=False, allow_blank=True, max_length=300)


class AssignmentBatchWriteSerializer(serializers.Serializer):
    judges = serializers.ListField(child=serializers.CharField())
    projects = serializers.ListField(child=serializers.CharField())


class AutoAssignmentWriteSerializer(serializers.Serializer):
    target = serializers.IntegerField(required=False, min_value=1)
    max_load = serializers.IntegerField(required=False, min_value=1, allow_null=True)
    seed = serializers.CharField(required=False, allow_blank=False)
    dry_run = serializers.BooleanField(default=True)


class ExclusionWriteSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300)
    expected_digest = serializers.CharField(required=False, allow_blank=False, max_length=64)


class InclusionWriteSerializer(serializers.Serializer):
    expected_digest = serializers.CharField(required=False, allow_blank=False, max_length=64)


class RebalanceWriteSerializer(serializers.Serializer):
    dry_run = serializers.BooleanField(default=True)
    max_load = serializers.IntegerField(required=False, min_value=1)


class RebalanceMoveSerializer(serializers.Serializer):
    assignment = serializers.CharField()
    from_judge = serializers.CharField()
    from_judge_name = serializers.CharField()
    to_judge = serializers.CharField()
    to_judge_name = serializers.CharField()
    project = serializers.CharField()
    project_title = serializers.CharField()
    track = serializers.CharField()
    track_name = serializers.CharField()


class RebalanceProposalSerializer(serializers.Serializer):
    max_load = serializers.IntegerField()
    moves = RebalanceMoveSerializer(many=True)
    skipped = serializers.ListField(child=serializers.DictField())


class RebalanceResponseSerializer(serializers.Serializer):
    dry_run = serializers.BooleanField()
    moved = serializers.IntegerField()
    batch_created = serializers.BooleanField()
    max_load = serializers.IntegerField()
    moves = RebalanceMoveSerializer(many=True)
    skipped = serializers.ListField(child=serializers.DictField())


class JudgeForecastSerializer(serializers.Serializer):
    judge = serializers.CharField()
    name = serializers.CharField()
    tracks = serializers.ListField(child=serializers.CharField())
    assigned = serializers.IntegerField()
    submitted = serializers.IntegerField()
    drafts = serializers.IntegerField()
    remaining = serializers.IntegerField()
    status = serializers.CharField()
    pace_minutes = serializers.FloatField(allow_null=True)
    minutes_left = serializers.FloatField(allow_null=True)
    projected_finish = serializers.DateTimeField(allow_null=True)
    first_assigned_at = serializers.DateTimeField(allow_null=True)
    first_submitted_at = serializers.DateTimeField(allow_null=True)
    last_submitted_at = serializers.DateTimeField(allow_null=True)
    at_risk = serializers.BooleanField()
    reasons = serializers.ListField(child=serializers.CharField())


class ForecastSerializer(serializers.Serializer):
    now = serializers.DateTimeField()
    judging_close_at = serializers.DateTimeField(allow_null=True)
    stalled_hours = serializers.FloatField()
    judges = JudgeForecastSerializer(many=True)
    projected_finish = serializers.DateTimeField(allow_null=True)
    at_risk = serializers.BooleanField()
    at_risk_count = serializers.IntegerField()


class CommandCenterSerializer(serializers.Serializer):
    event = serializers.CharField()
    forecast = ForecastSerializer()
    proposal = RebalanceProposalSerializer()


class TeamRefSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    name = serializers.CharField()


class JudgeTrackRefSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    name = serializers.CharField()


class JudgeAnswerSerializer(serializers.Serializer):
    question = serializers.CharField(help_text="Custom question public id.")
    prompt = serializers.CharField()
    value = serializers.CharField(allow_null=True)
    is_public = serializers.BooleanField(help_text="Whether the gallery may show this answer.")


class JudgeProjectSerializer(serializers.Serializer):
    """The project as a judge or a manager reads it: everything a rubric needs."""

    public_id = serializers.CharField()
    title = serializers.CharField()
    summary = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    team = TeamRefSerializer()
    track = JudgeTrackRefSerializer(allow_null=True)
    repo_url = serializers.CharField(allow_null=True, allow_blank=True)
    live_url = serializers.CharField(allow_null=True, allow_blank=True)
    demo_video_url = serializers.CharField(allow_null=True, allow_blank=True)
    answers = JudgeAnswerSerializer(many=True)


class PairwiseJudgeProjectSerializer(JudgeProjectSerializer):
    """Project detail plus the media a pairwise comparison shows side by side."""

    images = serializers.ListField(child=serializers.DictField())
    thumbnail = serializers.CharField(allow_null=True)


class CriterionSerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    weight = serializers.CharField(help_text="Decimal as sent, for example '0.400'.")
    min_score = serializers.IntegerField()
    max_score = serializers.IntegerField()
    position = serializers.IntegerField()


class RubricSerializer(serializers.Serializer):
    """The active rubric. ``version`` increments on every replacement."""

    version = serializers.IntegerField()
    criteria = CriterionSerializer(many=True)


class JudgeTrackSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    name = serializers.CharField()


class JudgeListEntrySerializer(serializers.Serializer):
    """A judge as a roster shows them: a display name, never an email address."""

    public_id = serializers.CharField()
    name = serializers.CharField()
    tracks = JudgeTrackSerializer(many=True)


class JudgeListSerializer(serializers.Serializer):
    judges = JudgeListEntrySerializer(many=True)


class JudgeCreatedSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    name = serializers.CharField()
    tracks = serializers.ListField(child=serializers.CharField())


class JudgeTracksSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    tracks = serializers.ListField(child=serializers.CharField())


class ReviewDetailSerializer(serializers.Serializer):
    """One review. Null fields mean the judge has not started it yet."""

    review_id = serializers.CharField(allow_null=True)
    status = serializers.CharField(allow_null=True, help_text="null, 'draft' or 'submitted'.")
    criteria = serializers.DictField(child=serializers.IntegerField())
    comment = serializers.CharField(allow_blank=True, allow_null=True)
    submitted_at = serializers.DateTimeField(allow_null=True)
    rubric_version = serializers.IntegerField(allow_null=True)
    project_revision = serializers.IntegerField(allow_null=True)


class JudgeReviewSerializer(serializers.Serializer):
    project = JudgeProjectSerializer()
    rubric = CriterionSerializer(many=True)
    review = ReviewDetailSerializer()


class ReviewWriteSerializer(serializers.Serializer):
    scores = serializers.DictField(
        child=serializers.IntegerField(),
        required=False,
        help_text="Criterion key to score, inside that criterion's own min and max.",
    )
    comment = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class JudgeInviteSerializer(serializers.Serializer):
    """One minted judge invite. The token is returned once, inside the URL."""

    token = serializers.CharField()
    email = serializers.EmailField()
    url = serializers.CharField(help_text="Relative /judge-invite?token=... link.")
    expires_at = serializers.DateTimeField()


class JudgeInvitesCreatedSerializer(serializers.Serializer):
    invites = JudgeInviteSerializer(many=True)


class JudgeInviteAcceptedSerializer(serializers.Serializer):
    event = serializers.CharField()
    judge = serializers.CharField(help_text="Public id of the judge role just created.")


class ConflictSerializer(serializers.Serializer):
    judge = serializers.CharField()
    judge_name = serializers.CharField()
    team = serializers.CharField()
    team_name = serializers.CharField()
    reason = serializers.CharField(allow_blank=True)
    source = serializers.CharField(help_text="'organizer' when recorded by staff, "
                                        "'judge' when self-declared.")


class ConflictListSerializer(serializers.Serializer):
    conflicts = ConflictSerializer(many=True)


class ConflictCreatedSerializer(serializers.Serializer):
    judge = serializers.CharField(required=False,
                                  help_text="Absent when the judge declared it themself.")
    team = serializers.CharField()
    reason = serializers.CharField(allow_blank=True)
    source = serializers.CharField()


class AssignmentSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    event = serializers.CharField()
    judge = serializers.CharField()
    project = JudgeProjectSerializer()
    review_status = serializers.CharField(allow_null=True)


class AssignmentListSerializer(serializers.Serializer):
    assignments = AssignmentSerializer(many=True)


class AssignmentCreatedSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    judge = serializers.CharField()
    project = serializers.CharField()


class AssignmentSkippedSerializer(serializers.Serializer):
    judge = serializers.CharField()
    project = serializers.CharField()
    reason = serializers.CharField(help_text="The eligibility rule that refused this pair.")


class AssignmentBatchResultSerializer(serializers.Serializer):
    created = AssignmentCreatedSerializer(many=True)
    skipped = AssignmentSkippedSerializer(many=True)


class AutoAssignmentProposedSerializer(serializers.Serializer):
    judge = serializers.CharField()
    project = serializers.CharField()


class AutoAssignmentUnfilledSerializer(serializers.Serializer):
    project = serializers.CharField()
    target = serializers.IntegerField()
    assigned = serializers.IntegerField()
    reason = serializers.CharField(
        help_text="Why the planner could not fill this slot. 'unfilled by this planner' "
                  "is a valid, and common, answer."
    )


class AutoAssignmentResultSerializer(serializers.Serializer):
    dry_run = serializers.BooleanField(help_text="True when nothing was written.")
    created = AssignmentCreatedSerializer(many=True)
    proposed = AutoAssignmentProposedSerializer(many=True)
    unfilled = AutoAssignmentUnfilledSerializer(many=True)
    batch_created = serializers.BooleanField()


class PairProgressSerializer(serializers.Serializer):
    assigned_projects = serializers.IntegerField()
    comparisons = serializers.IntegerField()
    total_pairs = serializers.IntegerField()
    target_per_project = serializers.IntegerField()
    projects_complete = serializers.IntegerField()
    project_counts = serializers.DictField(child=serializers.IntegerField())
    done = serializers.BooleanField()
    reason = serializers.CharField(
        help_text="'in_progress', 'coverage_reached' or 'pairs_exhausted'."
    )


class ComparisonSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    left = serializers.CharField()
    right = serializers.CharField()
    winner = serializers.CharField(allow_null=True, help_text="Null records an abstention.")
    created_at = serializers.DateTimeField()
    retracted_at = serializers.DateTimeField(allow_null=True)


class ComparisonListSerializer(serializers.Serializer):
    comparisons = ComparisonSerializer(many=True)


class NextPairSerializer(serializers.Serializer):
    """The next pair this judge should compare, or null when there is none left."""

    pair = serializers.DictField(allow_null=True, help_text="left and right when a pair exists.")
    left = PairwiseJudgeProjectSerializer(allow_null=True)
    right = PairwiseJudgeProjectSerializer(allow_null=True)
    done = serializers.BooleanField()
    progress = PairProgressSerializer()
    latest = ComparisonSerializer(
        allow_null=True,
        help_text="The caller's own most recent comparison, echoed for 30 seconds so an "
                  "optimistic UI can reconcile; null otherwise.",
    )


class ProgressJudgeSerializer(serializers.Serializer):
    judge = serializers.CharField()
    name = serializers.CharField()
    tracks = serializers.ListField(child=serializers.CharField())
    assigned = serializers.IntegerField()
    submitted = serializers.IntegerField()
    drafts = serializers.IntegerField()
    remaining = serializers.IntegerField()
    last_activity = serializers.DateTimeField(allow_null=True)
    status = serializers.CharField(
        help_text="'no assignments', 'not started', 'in progress' or 'done'."
    )


class ProgressProjectSerializer(serializers.Serializer):
    project = serializers.CharField()
    title = serializers.CharField()
    track = serializers.CharField(allow_null=True)
    submitted = serializers.IntegerField(help_text="Reviews actually submitted.")
    target = serializers.IntegerField(help_text="reviews_per_project for this event.")
    under_covered = serializers.BooleanField()


class ProgressTrackSerializer(serializers.Serializer):
    track = serializers.CharField()
    name = serializers.CharField()
    projects = serializers.IntegerField()
    submitted = serializers.IntegerField()
    target = serializers.IntegerField()
    coverage_percent = serializers.FloatField()


class EventProgressSerializer(serializers.Serializer):
    """Coverage counted from submitted reviews, not from assignment counts alone."""

    judges = ProgressJudgeSerializer(many=True)
    projects = ProgressProjectSerializer(many=True)
    tracks = ProgressTrackSerializer(many=True)


class ReviewExclusionSerializer(serializers.Serializer):
    review = serializers.CharField(help_text="Public id of the excluded review.")
    reason = serializers.CharField(help_text="Recorded reason; the review is not deleted.")


class ComparisonWriteSerializer(serializers.Serializer):
    left = serializers.CharField()
    right = serializers.CharField()
    winner = serializers.CharField(allow_null=True, required=False)


def judge_payload(role: EventRole) -> dict:
    """A judge role as the API exposes it: public ids and a display name, no email."""
    return {
        "public_id": role.public_id,
        "event": role.event.slug,
        "name": role.user.display_name or role.user.email.split("@")[0],
        "tracks": sorted(track.name for track in role.tracks.all()),
    }


def review_payload(pairs) -> list[dict]:
    """(review, weighted_score) pairs as the score listing shape."""
    return [
        {
            "review_id": review.public_id,
            "event": review.event.slug,
            "project": {"public_id": review.project.public_id, "title": review.project.title},
            "status": review.status,
            "criteria": review_values(review),
            "weighted_score": round(score, 2),
            "comment": review.comment,
            "submitted_at": review.submitted_at,
        }
        for review, score in pairs
    ]


def _not_a_judge() -> ApiError:
    return ApiError("not_a_judge", "Only judges can read scores.", status_code=403)


class JudgeScoresView(APIView):
    """``GET /api/v1/judge/scores`` - the caller's own reviews across events.

    A judge never gets another judge's scores through the query string: `?judge=`
    may only re-select a judge the caller already is.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="judge_scores",
        summary="The caller's own reviews across every event they judge.",
        description="401 anonymous, 403 not_a_judge without a judge role, and "
                    "403 when ?judge= names a judge the caller is not.",
        parameters=[OpenApiParameter(
            "judge", str, OpenApiParameter.QUERY, required=False,
            description="Judge public_id; only the caller's own ids are accepted.")],
        responses={200: JudgeScoresSerializer, **error_responses(401, 403)},
        tags=TAGS,
    )
    def get(self, request):
        roles = list(judge_roles(request.user).order_by("event__slug", "public_id"))
        if not roles:
            raise _not_a_judge()
        wanted = (request.query_params.get("judge") or "").strip()
        if wanted and wanted not in {role.public_id for role in roles}:
            raise ApiError("forbidden", "You can only read your own scores.", status_code=403)
        selected = [role for role in roles if not wanted or role.public_id == wanted]
        wanted_pks = {role.pk for role in selected}
        reviews = [review for review in judge_reviews(request.user)
                   if review.judge_id in wanted_pks]
        pairs = scored_reviews(reviews)
        return Response({
            "judge": judge_payload(selected[0]) if len(selected) == 1 else None,
            "judges": [judge_payload(role) for role in roles],
            "reviews": review_payload(pairs),
        })


class EventJudgeScoresView(APIView):
    """``GET /api/v1/events/{slug}/judges/{judge_id}/scores``.

    200 for the judge themself and for organizers of the event, 403 for everyone
    else including other judges, 401 anonymous. The judge id is only resolved
    once the caller is allowed to know whether it exists.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_judge_scores",
        summary="One judge's reviews in one event.",
        description="200 for that judge themself and for organizers of the event, "
                    "403 for everyone else including other judges, 404 for an unknown "
                    "judge id once the caller may know it exists.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
            OpenApiParameter("judge_id", str, OpenApiParameter.PATH,
                             description="Judge role public id."),
        ],
        responses={200: JudgeScoresSerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug: str, judge_id: str):
        event = get_event_by_slug(slug)
        if event is None:
            raise ApiError("event_not_found", f"No event with slug {slug!r}.", status_code=404)
        organizer = is_organizer(request.user, event)
        role = EventRole.objects.filter(event=event, public_id=judge_id, role=Role.JUDGE).first()
        if organizer:
            if role is None:
                raise ApiError("judge_not_found", f"No judge {judge_id!r} in {event.name}.",
                               status_code=404)
        elif role is None or role.user_id != request.user.pk:
            # Refused before the id is resolved: another judge must not be able to
            # probe which judge ids exist by watching 403 turn into 404.
            raise ApiError("forbidden", "Only this judge and the organizers may read these "
                                        "scores.", status_code=403)
        pairs = scored_reviews(reviews_for_judge_role(role))
        return Response({"judge": judge_payload(role), "reviews": review_payload(pairs)})


def _event(slug: str) -> Event:
    event = get_event_by_slug(slug)
    if event is None:
        raise ApiError("event_not_found", f"No event with slug {slug!r}.", status_code=404)
    return event


def _criterion_payload(item: Criterion) -> dict:
    return {
        "key": item.key, "name": item.name, "description": item.description,
        "weight": str(item.weight), "min_score": item.min_score,
        "max_score": item.max_score, "position": item.position,
    }


def _review_detail(review: Review | None) -> dict:
    if review is None:
        return {"review_id": None, "status": None, "criteria": {}, "comment": "",
                "submitted_at": None, "rubric_version": None, "project_revision": None}
    return {
        "review_id": review.public_id,
        "status": review.status,
        "criteria": review_values(review),
        "comment": review.comment,
        "submitted_at": review.submitted_at,
        "rubric_version": review.rubric_version,
        "project_revision": review.project_revision,
    }


def _project_detail(project: Project) -> dict:
    return {
        "public_id": project.public_id,
        "title": project.title,
        "summary": project.summary,
        "description": project.description,
        "team": {"public_id": project.team.public_id, "name": project.team.name},
        "track": {"public_id": project.track.public_id, "name": project.track.name}
        if project.track_id else None,
        "repo_url": project.repo_url,
        "live_url": project.live_url,
        "demo_video_url": project.demo_video_url,
        "answers": [
            {"question": answer.question.public_id, "prompt": answer.question.prompt,
             "value": answer.value, "is_public": answer.question.is_public}
            for answer in project.answers.all()
        ],
    }


class EventRubricView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_rubric",
        summary="Read the event's active judging rubric.",
        description="Judges and managers only. The version is what a review records so a "
                    "later rubric change cannot silently reinterpret an old score.",
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: RubricSerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        event = _event(slug)
        policy.require_judge_or_manager(request.user, event)
        criteria = policy.rubric_criteria(event)
        rubric = criteria[0].rubric if criteria else None
        return Response({"version": rubric.version if rubric else 0,
                         "criteria": [_criterion_payload(item) for item in criteria]})

    @extend_schema(
        operation_id="event_rubric_replace",
        summary="Replace the event's rubric with a new versioned one.",
        description="Manager only. The whole rubric is replaced in one transaction and the "
                    "version increments; existing reviews keep the version they were scored "
                    "against.",
        request=RubricWriteSerializer,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: RubricSerializer, **error_responses(400, 401, 403, 404)},
        tags=TAGS,
    )
    def put(self, request, slug: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        serializer = RubricWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rubric = services.replace_rubric(request.user, event, serializer.validated_data["criteria"])
        criteria = policy.rubric_criteria(event)
        return Response({"version": rubric.version,
                         "criteria": [_criterion_payload(item) for item in criteria]})


class EventJudgesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_judges",
        summary="List the judges of an event with their tracks.",
        description="A judge sees the roster so they can declare conflicts; a participant "
                    "does not. No email address is ever returned.",
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: JudgeListSerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        event = _event(slug)
        roles = policy.visible_judges(request.user, event)
        return Response({"judges": [
            {"public_id": row.public_id, "name": row.user.display_name or row.user.email.split("@")[0],
             "tracks": [{"public_id": track.public_id, "name": track.name} for track in row.tracks.all()]}
            for row in roles
        ]})

    @extend_schema(
        operation_id="event_judge_add",
        summary="Add a judge to the event, creating the account if needed.",
        description="Manager only. An address that already has an account gains a judge role; "
                    "an unknown one creates a placeholder account that cannot sign in until "
                    "an invite is accepted. Every change is audited.",
        request=JudgeWriteSerializer,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={201: JudgeCreatedSerializer, **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        serializer = JudgeWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = services.add_judge(request.user, event, serializer.validated_data["email"],
                                  serializer.validated_data.get("tracks", []))
        return Response({"public_id": role.public_id, "name": role.user.display_name or role.user.email.split("@")[0],
                         "tracks": list(role.tracks.values_list("public_id", flat=True))}, status=201)


class EventJudgeDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_judge_update",
        summary="Change which tracks a judge may review.",
        description="Manager only. Widening a judge's tracks never invalidates existing "
                    "assignments or reviews.",
        request=TracksWriteSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
            OpenApiParameter("judge_id", str, OpenApiParameter.PATH,
                             description="Judge role public id."),
        ],
        responses={200: JudgeTracksSerializer, **error_responses(400, 401, 403, 404)},
        tags=TAGS,
    )
    def patch(self, request, slug: str, judge_id: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        serializer = TracksWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = services.update_judge_tracks(request.user, event, judge_id, serializer.validated_data["tracks"])
        return Response({"public_id": role.public_id,
                         "tracks": list(role.tracks.values_list("public_id", flat=True))})

    @extend_schema(
        operation_id="event_judge_remove",
        summary="Remove a judge role from the event.",
        description="Manager only. Submitted reviews are kept and stay attributable; "
                    "unsubmitted assignments for the judge are removed with the role.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
            OpenApiParameter("judge_id", str, OpenApiParameter.PATH,
                             description="Judge role public id."),
        ],
        responses={204: None, **error_responses(401, 403, 404, 409)},
        tags=TAGS,
    )
    def delete(self, request, slug: str, judge_id: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        services.remove_judge(request.user, event, judge_id)
        return Response(status=204)


class JudgeInvitesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_judge_invites",
        summary="Mint judge invite links for a list of email addresses.",
        description="Manager only. Each link is single use, expires in 14 days, and is "
                    "queued to the event's private outbox for the organizer to hand over. "
                    "Re-inviting retires the previous link for that address.",
        request=InviteWriteSerializer,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={201: JudgeInvitesCreatedSerializer,
                   **error_responses(400, 401, 403, 404)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        serializer = InviteWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitations = services.create_judge_invites(
            request.user, event, serializer.validated_data["emails"],
            serializer.validated_data.get("tracks", []),
        )
        return Response({"invites": invitations}, status=201)


class AcceptJudgeInviteView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="judge_invite_accept",
        summary="Accept a judge invite and gain the judge role.",
        description="Refused with 410 judge_invite_invalid when the link was replaced, "
                    "revoked or has expired, and 409 when the caller already holds a "
                    "conflicting role in the event.",
        request=None,
        parameters=[OpenApiParameter("token", str, OpenApiParameter.PATH,
                                     description="One-time invite token.")],
        responses={201: JudgeInviteAcceptedSerializer, **error_responses(401, 409, 410)},
        tags=TAGS,
    )
    def post(self, request, token: str):
        role = services.accept_judge_invite(request.user, token)
        return Response({"event": role.event.slug, "judge": role.public_id}, status=201)


class EventConflictsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_conflicts",
        summary="List the recorded conflicts of interest in an event.",
        description="Judges see their own; organizers see every conflict. Declared and "
                    "organizer-recorded conflicts both appear, distinguished by source.",
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: ConflictListSerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        event = _event(slug)
        rows = policy.visible_conflicts(request.user, event)
        return Response({"conflicts": [
            {"judge": row.judge.public_id, "judge_name": row.judge.user.display_name
             or row.judge.user.email.split("@")[0], "team": row.team.public_id,
             "team_name": row.team.name, "reason": row.reason, "source": row.source}
            for row in rows
        ]})

    @extend_schema(
        operation_id="event_conflict_create",
        summary="Record a conflict between a judge and a team (manager).",
        description="Manager only. The assignment planner refuses every judge/team pair "
                    "that appears here; declaring one is not a disqualification and is not "
                    "visible to the team.",
        request=ConflictWriteSerializer,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={201: ConflictCreatedSerializer, **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        serializer = ConflictWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if not values.get("judge"):
            raise ApiError("invalid", "Organizer conflicts require a judge id.",
                           fields={"judge": ["This field is required."]})
        conflict = services.add_conflict(request.user, event, values["judge"], values["team"],
                                         values.get("reason", ""))
        return Response({"judge": conflict.judge.public_id, "team": conflict.team.public_id,
                         "reason": conflict.reason, "source": conflict.source}, status=201)


class JudgeConflictView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="judge_conflict_declare",
        summary="Declare your own conflict with a team.",
        description="A judge only. Recording a conflict is not a zero score and carries no "
                    "judgement on the team; the planner stops assigning the pair immediately.",
        request=ConflictWriteSerializer,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={201: ConflictCreatedSerializer, **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        event = _event(slug)
        policy.require_judge(request.user, event)
        serializer = ConflictWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        conflict = services.declare_conflict(request.user, event, values["team"],
                                              values.get("reason", ""))
        return Response({"team": conflict.team.public_id, "reason": conflict.reason,
                         "source": conflict.source}, status=201)


def _assignment_payload(row: Assignment) -> dict:
    review = getattr(row, "review", None)
    return {
        "public_id": row.public_id,
        "event": row.event.slug,
        "judge": row.judge.public_id,
        "project": _project_detail(row.project),
        "review_status": review.status if review else None,
    }


class JudgeAssignmentsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="judge_assignments",
        summary="The caller's own review queue across every event.",
        description="A judge sees only their own queue. There is no parameter that widens "
                    "this: another judge's workload is not this judge's business.",
        parameters=[OpenApiParameter("judge", str, OpenApiParameter.QUERY, required=False,
                                     description="Judge public_id; only the caller's own id "
                                                 "is accepted.")],
        responses={200: AssignmentListSerializer, **error_responses(401, 403)},
        tags=TAGS,
    )
    def get(self, request):
        wanted = (request.query_params.get("judge") or "").strip()
        rows = policy.visible_assignments(request.user, judge_public_id=wanted or None).prefetch_related(
            "project__answers__question", "review"
        )
        return Response({"assignments": [_assignment_payload(row) for row in rows]})


class EventAssignmentsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_assignments",
        summary="Read the assignment table of an event.",
        description="An organizer sees every assignment; a judge sees their own rows only.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
            OpenApiParameter("judge", str, OpenApiParameter.QUERY, required=False,
                             description="Filter to one judge public id, where permitted."),
        ],
        responses={200: AssignmentListSerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        event = _event(slug)
        wanted = (request.query_params.get("judge") or "").strip()
        rows = policy.visible_assignments(
            request.user, event, judge_public_id=wanted or None
        ).prefetch_related("project__answers__question", "review")
        return Response({"assignments": [_assignment_payload(row) for row in rows]})

    @extend_schema(
        operation_id="event_assignments_create",
        summary="Create assignments for explicit judge/project pairs.",
        description="Manager only. Every pair is re-checked under lock against track, "
                    "conflict and duplicate rules; a refused pair comes back in skipped "
                    "with the reason and does not stop the rest of the batch.",
        request=AssignmentBatchWriteSerializer,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={201: AssignmentBatchResultSerializer,
                   **error_responses(400, 401, 403, 404)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        serializer = AssignmentBatchWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.assign_batch(request.user, event, serializer.validated_data["judges"],
                                       serializer.validated_data["projects"])
        return Response({
            "created": [{"public_id": row.public_id, "judge": row.judge.public_id,
                         "project": row.project.public_id} for row in result["created"]],
            "skipped": result["skipped"],
        }, status=201)


class EventAssignmentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_assignment_delete",
        summary="Delete one assignment.",
        description="Manager only. A submitted review for the assignment is kept and stays "
                    "attributable; deleting the assignment does not erase the judgment.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
            OpenApiParameter("assignment_id", str, OpenApiParameter.PATH,
                             description="Assignment public id."),
        ],
        responses={204: None, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def delete(self, request, slug: str, assignment_id: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        services.remove_assignment(request.user, event, assignment_id)
        return Response(status=204)


class AutoAssignmentsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_assignments_auto",
        summary="Preview or apply the automatic assignment planner.",
        description="Manager only. dry_run defaults to true, so the default call changes "
                    "nothing and only reports a proposal. Slots the planner could not fill "
                    "come back in unfilled with a reason; a free slot count is never a "
                    "claim that a feasible assignment exists.",
        request=AutoAssignmentWriteSerializer,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: AutoAssignmentResultSerializer,
                   **error_responses(400, 401, 403, 404)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        serializer = AutoAssignmentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.auto_assign(request.user, event, **serializer.validated_data)
        return Response({
            "dry_run": result["dry_run"],
            "created": [{"public_id": row.public_id, "judge": row.judge.public_id,
                         "project": row.project.public_id} for row in result["created"]],
            "proposed": result["proposed"],
            "unfilled": result["unfilled"],
            "batch_created": result.get("batch") is not None,
        })


class JudgeReviewView(APIView):
    permission_classes = [IsAuthenticated]

    def _project_and_review(self, request, slug: str, prj_id: str):
        event = _event(slug)
        project = policy.judge_project(request.user, event, prj_id)
        review = visible_reviews(request.user, event).filter(project=project).first()
        return event, project, review

    @extend_schema(
        operation_id="judge_review",
        summary="Read one project with its rubric and the caller's review of it.",
        description="An assigned judge only. Answers marked non-public are shown to the "
                    "judge for context but are not part of the public gallery.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
            OpenApiParameter("prj_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
        ],
        responses={200: JudgeReviewSerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug: str, prj_id: str):
        event, project, review = self._project_and_review(request, slug, prj_id)
        criteria = policy.rubric_criteria(event)
        return Response({"project": _project_detail(project),
                         "rubric": [_criterion_payload(item) for item in criteria],
                         "review": _review_detail(review)})

    @extend_schema(
        operation_id="judge_review_draft",
        summary="Save or update your draft review without submitting it.",
        description="A draft is not a score in the results. The write is refused with 409 "
                    "after the judging window closes, and the server rechecks that rule "
                    "under lock rather than trusting the clock on the page.",
        request=ReviewWriteSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
            OpenApiParameter("prj_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
        ],
        responses={200: ReviewDetailSerializer, **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def put(self, request, slug: str, prj_id: str):
        event = _event(slug)
        project = policy.judge_project(request.user, event, prj_id)
        services.check_review_access(request.user, event, project)
        data = request.data if hasattr(request.data, "get") else {}
        review = services.save_review_draft(
            request.user, event, project, data.get("scores"), data.get("comment", ""),
        )
        return Response(_review_detail(review))


class SubmitJudgeReviewView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="judge_review_submit",
        summary="Submit your review of one project for the results.",
        description="A submitted review is final for scoring purposes and is recorded "
                    "against the rubric version it was scored under. The window is "
                    "re-checked under lock, so a submit that races the close is refused "
                    "with 409 rather than silently accepted.",
        request=ReviewWriteSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
            OpenApiParameter("prj_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
        ],
        responses={200: ReviewDetailSerializer, **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request, slug: str, prj_id: str):
        event = _event(slug)
        project = policy.judge_project(request.user, event, prj_id)
        services.check_review_access(request.user, event, project)
        data = request.data if hasattr(request.data, "get") else {}
        review = services.submit_review(
            request.user, event, project, data.get("scores"), data.get("comment", ""),
        )
        return Response(_review_detail(review))


class NextPairView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="judge_pair_next",
        summary="The next pair this judge should compare (pairwise mode).",
        description="Judge only, and only in pairwise mode. Selection is coverage-first and "
                    "deterministic for a given event, judge and comparison set, so a reload "
                    "returns the same pair. A null pair with done true means the graph is "
                    "exhausted or the per-project target is met, not that judging is over.",
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: NextPairSerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        event = _event(slug)
        state = services.pairwise_state(request.user, event)
        pair = state["pair"]
        details = None if pair is None else {
            "left": _pairwise_project_detail(pair[0]), "right": _pairwise_project_detail(pair[1]),
        }
        return Response({"pair": details, "left": details["left"] if details else None,
                         "right": details["right"] if details else None,
                         "done": state["done"], "progress": state["progress"],
                         "latest": _comparison_payload(state["latest"]) if state["latest"] else None})


def _pairwise_project_detail(project):
    data = _project_detail(project)
    data["images"] = [{"url": image.image.url, "caption": image.caption} for image in project.images.all()]
    data["thumbnail"] = project.thumbnail.url if project.thumbnail else None
    return data


def _comparison_payload(comparison):
    return {"public_id": comparison.public_id, "left": comparison.left.public_id,
            "right": comparison.right.public_id,
            "winner": comparison.winner.public_id if comparison.winner_id else None,
            "created_at": comparison.created_at, "retracted_at": comparison.retracted_at}


class JudgeComparisonView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="judge_comparisons",
        summary="Your own pairwise comparisons in an event.",
        description="Judge only. Reading someone else's comparisons is 403, and a retracted "
                    "comparison stays listed with retracted_at set rather than disappearing.",
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: ComparisonListSerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        event = _event(slug)
        role = policy.require_judge(request.user, event)
        if request.query_params.get("judge", role.public_id) != role.public_id:
            raise ApiError("forbidden", "You can only read your own comparisons.", status_code=403)
        return Response({"comparisons": [_comparison_payload(item)
                                        for item in policy.visible_comparisons(request.user, event)]})

    @extend_schema(
        operation_id="judge_comparison_create",
        summary="Record one pairwise comparison, or an abstention.",
        description="Judge only, pairwise mode only. Send a null winner to record a skip; "
                    "an abstention counts as the pair having been seen by the selector. "
                    "Retracting later does not delete the record.",
        request=ComparisonWriteSerializer,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={201: ComparisonSerializer, **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        event = _event(slug)
        policy.require_judge(request.user, event)
        serializer = ComparisonWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comparison = services.save_comparison(
            request.user, event, serializer.validated_data["left"], serializer.validated_data["right"],
            serializer.validated_data.get("winner"),
        )
        return Response(_comparison_payload(comparison), status=201)


class UndoComparisonView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="judge_comparison_undo",
        summary="Retract your most recent pairwise comparison.",
        description="Judge only, and only for your own comparison. The row is marked "
                    "retracted with a timestamp rather than deleted, so the record of what "
                    "was answered stays auditable.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
            OpenApiParameter("comparison_id", str, OpenApiParameter.PATH,
                             description="Comparison public id."),
        ],
        responses={200: ComparisonSerializer, **error_responses(401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request, slug: str, comparison_id: str):
        comparison = services.undo_comparison(request.user, _event(slug), comparison_id)
        return Response(_comparison_payload(comparison))


class EventProgressView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_progress",
        summary="Judging coverage of an event, counted from submitted reviews.",
        description="Manager only. Submitted reviews count as evidence; assignment counts do "
                    "not. under_covered marks a project that still has fewer submitted "
                    "reviews than reviews_per_project.",
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: EventProgressSerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        return Response(policy.progress(event))


class EventCommandCenterView(APIView):
    """``GET /api/v1/events/{slug}/command-center``: the pace forecast and proposal.

    200 for organizers of the event, 403 for a judge or participant, 401
    anonymous, 404 for an unknown slug. The payload is read-only: it changes
    nothing, so the page can poll it without writing.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_command_center",
        summary="Judge pace forecast, projected finish and rebalance proposal.",
        description="Per judge: assigned/submitted/drafts, median minutes per review from live "
                    "submissions, remaining work, projected finish and at-risk reasons. The "
                    "proposal moves only untouched assignments off at-risk judges.",
        responses={200: CommandCenterSerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        return Response(services.command_center(request.user, event))


class EventRebalanceView(APIView):
    """``POST /api/v1/events/{slug}/assignments/rebalance``: preview or apply.

    ``{"dry_run": true}`` previews; ``{"dry_run": false}`` applies the same plan
    in one transaction and records an AssignmentBatch with method "rebalance".
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_rebalance",
        summary="Preview or apply a rebalance of untouched assignments.",
        description="Only assignments with no draft and no submission move, and only onto an "
                    "in-track, non-conflicted judge under max_load. 403 for anyone but an "
                    "organizer, 409 when a judge started work while the plan was being applied.",
        request=RebalanceWriteSerializer,
        responses={200: RebalanceResponseSerializer, **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        serializer = RebalanceWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.rebalance(request.user, event, **serializer.validated_data)
        return Response(result)


class ReviewExclusionView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="review_exclusion_create",
        summary="Exclude one review from the results, with a recorded reason.",
        description="Manager only. The review is kept and stays attributable; the reason is "
                    "audited. Excluding is an explicit authorized action, never a silent "
                    "rewrite of a published result.",
        request=ExclusionWriteSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
            OpenApiParameter("review_id", str, OpenApiParameter.PATH,
                             description="Review public id."),
        ],
        responses={201: ReviewExclusionSerializer, **error_responses(400, 401, 403, 404)},
        tags=TAGS,
    )
    def post(self, request, slug: str, review_id: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        review = visible_reviews(request.user, event).filter(public_id=review_id).first()
        if review is None:
            raise ApiError("review_not_found", "No review was found.", status_code=404)
        serializer = ExclusionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exclusion = services.exclude_review(
            request.user, review, serializer.validated_data["reason"],
            expected_digest=serializer.validated_data.get("expected_digest"),
        )
        return Response({"review": review.public_id, "reason": exclusion.reason}, status=201)

    @extend_schema(
        operation_id="review_exclusion_delete",
        summary="Put an excluded review back into the results.",
        description="Manager only. Clears the exclusion and is audited; the review itself "
                    "was never deleted, so re-including restores it unchanged.",
        request=InclusionWriteSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
            OpenApiParameter("review_id", str, OpenApiParameter.PATH,
                             description="Review public id."),
        ],
        responses={204: None, **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def delete(self, request, slug: str, review_id: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        review = visible_reviews(request.user, event).filter(public_id=review_id).first()
        if review is None:
            raise ApiError("review_not_found", "No review was found.", status_code=404)
        serializer = InclusionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.include_review(
            request.user, review,
            expected_digest=serializer.validated_data.get("expected_digest"),
        )
        return Response(status=204)
