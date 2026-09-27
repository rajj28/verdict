"""Projects, revisions, images and answers.

DRF serializers and viewsets. Thin: validate, delegate to services, return the envelope.
"""
from core.errors import ApiError
from core.pagination import VerdictPagination
from drf_spectacular.utils import extend_schema
from events.models import Event
from events.policy import get_event_by_slug, is_organizer
from projects.models import Project, ProjectStatus
from projects.services import create_project
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response


class ProjectSerializer(serializers.ModelSerializer):
    """Public shape of a project: public ids and display names, never integer keys."""

    track = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    team = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    event = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = Project
        fields = [
            "public_id", "title", "summary", "status", "revision", "track", "team", "event",
            "first_submitted_at", "last_submitted_at",
        ]


class ProjectCreateSerializer(serializers.Serializer):
    """Shape check only; the window, team and length rules live in the service."""

    title = serializers.CharField(required=False, allow_blank=True, max_length=200)
    summary = serializers.CharField(required=False, allow_blank=True, max_length=400)
    description = serializers.CharField(required=False, allow_blank=True)
    track = serializers.CharField(required=False, allow_blank=True)
    repo_url = serializers.URLField(required=False, allow_blank=True)
    live_url = serializers.URLField(required=False, allow_blank=True)
    demo_video_url = serializers.URLField(required=False, allow_blank=True)
    tech_tags = serializers.ListField(
        child=serializers.CharField(max_length=24), required=False, max_length=10
    )


def event_or_404(slug: str) -> Event:
    event = get_event_by_slug(slug)
    if event is None:
        raise ApiError("event_not_found", f"No event with slug {slug!r}.", status_code=404)
    return event


class EventProjectListCreate(GenericAPIView):
    """``GET|POST /api/v1/events/{slug}/projects``.

    GET lists the event's submitted projects (public where the gallery is public);
    POST starts the caller's team's draft. The two methods have genuinely
    different rules, so the permissions are declared per method rather than
    inherited from a default that would be wrong for one of them.
    """

    pagination_class = VerdictPagination

    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(
        operation_id="event_projects",
        summary="List the event's submitted projects (public) or start your team's draft.",
        responses={200: ProjectSerializer(many=True), 201: ProjectSerializer,
                   400: None, 401: None, 403: None, 404: None, 409: None},
        tags=["projects"],
    )
    def get(self, request, slug: str):
        event = event_or_404(slug)
        if not event.gallery_public and not is_organizer(request.user, event):
            raise ApiError("event_not_found", f"No event with slug {slug!r}.", status_code=404)
        projects = (
            Project.objects.filter(event=event, status=ProjectStatus.SUBMITTED)
            .select_related("team", "track", "event")
            .order_by("title", "id")
        )
        page = self.paginate_queryset(projects)
        serializer = ProjectSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(
        operation_id="event_project_create",
        summary="Create your team's draft project.",
        description="Window closed answers 403 window_closed before any validation; "
                    "no team 403 not_a_participant; a second active project 409 "
                    "team_has_project.",
        request=ProjectCreateSerializer,
        responses={201: ProjectSerializer, 400: None, 401: None, 403: None, 404: None,
                   409: None},
        tags=["projects"],
    )
    def post(self, request, slug: str):
        event = event_or_404(slug)
        serializer = ProjectCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = create_project(request.user, event, dict(serializer.validated_data))
        return Response(ProjectSerializer(project).data, status=201)
