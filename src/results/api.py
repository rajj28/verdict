"""Results API: preview, publish, public results, decision record, feedback.

Thin views: validate the request, delegate to services, return the envelope.
"""
from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.errors import ApiError
from events.policy import get_event_by_slug, is_organizer
from projects.policy import visible_project
from results import services
from results.policy import (
    can_see_feedback,
    get_publication,
    require_organizer,
)


def _get_event(slug: str):
    event = get_event_by_slug(slug)
    if event is None:
        raise ApiError("event_not_found", f"No event with slug {slug!r}.", status_code=404)
    return event


class ResultsPreviewView(APIView):
    """``GET /events/{slug}/results/preview`` — organizer only."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="results_preview",
        summary="Preview results (organizer).",
        responses={
            200: OpenApiResponse(description="Preview data."),
            403: OpenApiResponse(description="Not an organizer."),
            409: OpenApiResponse(description="No rubric or other state conflict."),
        },
    )
    def get(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        require_organizer(request.user, event)
        data = services.preview(event)
        return Response(data)


class ResultsPublishView(APIView):
    """``POST /events/{slug}/results/publish`` — organizer only."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="results_publish",
        summary="Publish results (organizer).",
        responses={
            201: OpenApiResponse(description="Publication created."),
            400: OpenApiResponse(description="Note required for re-publication."),
            403: OpenApiResponse(description="Not an organizer."),
            409: OpenApiResponse(description="Judging open, unranked projects, or no rubric."),
        },
    )
    def post(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        require_organizer(request.user, event)
        note = request.data.get("note", "")
        acknowledge_unranked = bool(request.data.get("acknowledge_unranked", False))
        pub = services.publish(
            request.user, event, note=note, acknowledge_unranked=acknowledge_unranked
        )
        return Response(
            {
                "pub_id": pub.public_id,
                "published_at": pub.published_at.isoformat(),
                "method": pub.method,
                "input_digest": pub.input_digest,
                "note": pub.note,
            },
            status=201,
        )


class ResultsPublicView(APIView):
    """``GET /events/{slug}/results`` — public after publication; organizers get preview."""

    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="results_public",
        summary="Public results for an event.",
        responses={
            200: OpenApiResponse(description="Results rows."),
            404: OpenApiResponse(description="Not published."),
        },
    )
    def get(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        # Organizers get the live preview instead.
        if request.user.is_authenticated and is_organizer(request.user, event):
            data = services.preview(event)
            return Response(data)
        data = services.public_results(event)
        return Response(data)


class PublicationDetailView(APIView):
    """``GET /events/{slug}/results/publications/{pub_id}`` — organizer only, decision record."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="publication_detail",
        summary="Decision record for a specific publication (organizer).",
    )
    def get(self, request: Request, slug: str, pub_id: str) -> Response:
        event = _get_event(slug)
        require_organizer(request.user, event)
        pub = get_publication(event, pub_id)
        return Response(services.decision_record(pub))


class PublicationVerifyView(APIView):
    """``POST /events/{slug}/results/publications/{pub_id}/verify`` — organizer only."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="publication_verify",
        summary="Verify a publication (organizer): recompute and compare.",
    )
    def post(self, request: Request, slug: str, pub_id: str) -> Response:
        event = _get_event(slug)
        require_organizer(request.user, event)
        pub = get_publication(event, pub_id)
        result = services.verify_publication(pub)
        return Response(result)


class FeedbackReleaseView(APIView):
    """``POST|DELETE /events/{slug}/feedback-release`` — organizer only."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="feedback_release",
        summary="Release team feedback (organizer).",
    )
    def post(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        require_organizer(request.user, event)
        pub = services.release_feedback(request.user, event)
        return Response(
            {"pub_id": pub.public_id, "feedback_released_at": pub.feedback_released_at.isoformat()},
            status=200,
        )

    @extend_schema(
        operation_id="feedback_retract",
        summary="Retract team feedback (organizer).",
    )
    def delete(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        require_organizer(request.user, event)
        pub = services.retract_feedback(request.user, event)
        return Response(
            {"pub_id": pub.public_id, "feedback_released_at": None},
            status=200,
        )


class ProjectFeedbackView(APIView):
    """``GET /events/{slug}/projects/{project_id}/feedback`` — team members after release."""

    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="project_feedback",
        summary="De-attributed feedback for a project (team members after release).",
    )
    def get(self, request: Request, slug: str, project_id: str) -> Response:
        event = _get_event(slug)
        from projects.models import Project

        project = Project.objects.filter(event=event, public_id=project_id).select_related("team").first()
        if project is None:
            raise ApiError("project_not_found", "No such project.", status_code=404)

        if not can_see_feedback(request.user, event, project):
            raise ApiError(
                "forbidden",
                "Only team members may see their project's feedback.",
                status_code=403,
            )
        # Check if feedback is released (organizers can always see it).
        if not is_organizer(request.user, event):
            from results.models import ResultPublication
            pub = ResultPublication.objects.filter(event=event).order_by("-published_at").first()
            if pub is None or pub.feedback_released_at is None:
                raise ApiError(
                    "not_released",
                    "Feedback has not been released for this event.",
                    status_code=403,
                )
        data = services.project_feedback(event, project)
        return Response(data)
