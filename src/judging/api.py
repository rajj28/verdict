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
        responses={200: JudgeScoresSerializer, 401: None, 403: None},
        tags=["judging"],
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
        responses={200: JudgeScoresSerializer, 401: None, 403: None, 404: None},
        tags=["judging"],
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

    def get(self, request, slug: str):
        event = _event(slug)
        policy.require_judge_or_manager(request.user, event)
        criteria = policy.rubric_criteria(event)
        rubric = criteria[0].rubric if criteria else None
        return Response({"version": rubric.version if rubric else 0,
                         "criteria": [_criterion_payload(item) for item in criteria]})

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

    def get(self, request, slug: str):
        event = _event(slug)
        roles = policy.visible_judges(request.user, event)
        return Response({"judges": [
            {"public_id": row.public_id, "name": row.user.display_name or row.user.email.split("@")[0],
             "tracks": [{"public_id": track.public_id, "name": track.name} for track in row.tracks.all()]}
            for row in roles
        ]})

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

    def patch(self, request, slug: str, judge_id: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        serializer = TracksWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = services.update_judge_tracks(request.user, event, judge_id, serializer.validated_data["tracks"])
        return Response({"public_id": role.public_id,
                         "tracks": list(role.tracks.values_list("public_id", flat=True))})

    def delete(self, request, slug: str, judge_id: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        services.remove_judge(request.user, event, judge_id)
        return Response(status=204)


class JudgeInvitesView(APIView):
    permission_classes = [IsAuthenticated]

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

    def post(self, request, token: str):
        role = services.accept_judge_invite(request.user, token)
        return Response({"event": role.event.slug, "judge": role.public_id}, status=201)


class EventConflictsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug: str):
        event = _event(slug)
        rows = policy.visible_conflicts(request.user, event)
        return Response({"conflicts": [
            {"judge": row.judge.public_id, "judge_name": row.judge.user.display_name
             or row.judge.user.email.split("@")[0], "team": row.team.public_id,
             "team_name": row.team.name, "reason": row.reason, "source": row.source}
            for row in rows
        ]})

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

    def get(self, request):
        wanted = (request.query_params.get("judge") or "").strip()
        rows = policy.visible_assignments(request.user, judge_public_id=wanted or None).prefetch_related(
            "project__answers__question", "review"
        )
        return Response({"assignments": [_assignment_payload(row) for row in rows]})


class EventAssignmentsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug: str):
        event = _event(slug)
        wanted = (request.query_params.get("judge") or "").strip()
        rows = policy.visible_assignments(
            request.user, event, judge_public_id=wanted or None
        ).prefetch_related("project__answers__question", "review")
        return Response({"assignments": [_assignment_payload(row) for row in rows]})

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

    def delete(self, request, slug: str, assignment_id: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        services.remove_assignment(request.user, event, assignment_id)
        return Response(status=204)


class AutoAssignmentsView(APIView):
    permission_classes = [IsAuthenticated]

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

    def get(self, request, slug: str, prj_id: str):
        event, project, review = self._project_and_review(request, slug, prj_id)
        criteria = policy.rubric_criteria(event)
        return Response({"project": _project_detail(project),
                         "rubric": [_criterion_payload(item) for item in criteria],
                         "review": _review_detail(review)})

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

    def get(self, request, slug: str):
        event = _event(slug)
        role = policy.require_judge(request.user, event)
        if request.query_params.get("judge", role.public_id) != role.public_id:
            raise ApiError("forbidden", "You can only read your own comparisons.", status_code=403)
        return Response({"comparisons": [_comparison_payload(item)
                                        for item in policy.visible_comparisons(request.user, event)]})

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

    def post(self, request, slug: str, comparison_id: str):
        comparison = services.undo_comparison(request.user, _event(slug), comparison_id)
        return Response(_comparison_payload(comparison))


class EventProgressView(APIView):
    permission_classes = [IsAuthenticated]

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
        responses={200: CommandCenterSerializer, 401: None, 403: None, 404: None},
        tags=["judging"],
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
        responses={200: RebalanceResponseSerializer, 401: None, 403: None, 404: None, 409: None},
        tags=["judging"],
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

    def post(self, request, slug: str, review_id: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        review = visible_reviews(request.user, event).filter(public_id=review_id).first()
        if review is None:
            raise ApiError("review_not_found", "No review was found.", status_code=404)
        serializer = ExclusionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exclusion = services.exclude_review(request.user, review, serializer.validated_data["reason"])
        return Response({"review": review.public_id, "reason": exclusion.reason}, status=201)

    def delete(self, request, slug: str, review_id: str):
        event = _event(slug)
        policy.require_manager(request.user, event)
        review = visible_reviews(request.user, event).filter(public_id=review_id).first()
        if review is None:
            raise ApiError("review_not_found", "No review was found.", status_code=404)
        services.include_review(request.user, review)
        return Response(status=204)
