"""Rubric, judges, assignments, reviews and scores.

DRF serializers and viewsets. Thin: validate, delegate to services, return the envelope.
"""
from core.errors import ApiError
from drf_spectacular.utils import OpenApiParameter, extend_schema
from events.models import EventRole, Role
from events.policy import get_event_by_slug, is_organizer, judge_roles
from judging.policy import (judge_reviews, review_values, reviews_for_judge_role,
                            scored_reviews)
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
