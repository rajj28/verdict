"""Projects, revisions, images and answers.

DRF serializers and viewsets. Thin: validate, delegate to services, return the envelope.
"""
from core.errors import ApiError
from core.pagination import VerdictPagination
from drf_spectacular.utils import OpenApiParameter, extend_schema
from events.models import Event
from events.policy import get_event_by_slug, is_organizer
from projects import services
from projects.models import Project
from projects.policy import (SORTS, can_edit_project, filter_gallery, public_projects,
                             visible_answers, visible_images, visible_project_by_id)
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.schema import error_responses

TAGS = ["projects"]


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


class GalleryProjectSerializer(serializers.ModelSerializer):
    """The gallery listing: the card fields plus the public links and tags."""

    track = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    team = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    event = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    detail = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            "public_id", "title", "summary", "status", "track", "team", "event", "tech_tags",
            "detail", "last_submitted_at",
        ]

    def get_detail(self, project: Project) -> str:
        return f"/events/{project.event.slug}/projects/{project.public_id}"


class ProjectDetailSerializer(serializers.ModelSerializer):
    """One project as the reader is allowed to see it.

    The private answers are included only for the team, organizers and assigned
    judges, so the serializer is built from the caller's view, not from the row.
    """

    track = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    team = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    event = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    team_name = serializers.SerializerMethodField()
    answers = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    revisions = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    can_withdraw = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            "public_id", "title", "summary", "description", "status", "status_reason",
            "revision", "track", "team", "team_name", "event", "tech_tags", "demo_video_url",
            "repo_url", "live_url", "thumbnail", "first_submitted_at", "last_submitted_at",
            "updated_at", "answers", "images", "revisions", "can_edit", "can_withdraw",
        ]

    def _viewer(self):
        return self.context.get("viewer")

    def get_team_name(self, project: Project) -> str:
        return project.team.name

    def get_answers(self, project: Project) -> list:
        return [
            {"question": answer.question.public_id, "prompt": answer.question.prompt,
             "is_public": answer.question.is_public, "value": answer.value}
            for answer in visible_answers(self._viewer(), project)
        ]

    def get_images(self, project: Project) -> list:
        return [
            {"position": image.position, "caption": image.caption, "url": image.image.url}
            for image in visible_images(project)
        ]

    def get_revisions(self, project: Project) -> list:
        if not can_edit_project(self._viewer(), project):
            return []
        return [
            {"number": revision.number, "created_at": revision.created_at,
             "receipt": revision.digest[:8]}
            for revision in project.revisions.all()
        ]

    def get_can_edit(self, project: Project) -> bool:
        return can_edit_project(self._viewer(), project)

    def get_can_withdraw(self, project: Project) -> bool:
        from teams.policy import is_team_owner

        return is_team_owner(self._viewer(), project.team) and project.is_active


class ProjectWriteSerializer(serializers.Serializer):
    """Shape check only; the window, ownership and length rules live in the service."""

    title = serializers.CharField(required=False, allow_blank=True, max_length=400)
    summary = serializers.CharField(required=False, allow_blank=True, max_length=400)
    description = serializers.CharField(required=False, allow_blank=True)
    track = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    repo_url = serializers.CharField(required=False, allow_blank=True)
    live_url = serializers.CharField(required=False, allow_blank=True)
    demo_video_url = serializers.CharField(required=False, allow_blank=True)
    tech_tags = serializers.ListField(
        child=serializers.CharField(max_length=40), required=False, max_length=20
    )


class ProjectPatchSerializer(ProjectWriteSerializer):
    """Same fields, all optional: PATCH only changes what it is sent.

    base_updated_at is the version the editor was looking at; a mismatch is
    409 stale_edit rather than a silent overwrite (BUILD-SEC section 16).
    """

    base_updated_at = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class DisqualifySerializer(serializers.Serializer):
    reason = serializers.CharField(allow_blank=True, max_length=2000)
    expected_digest = serializers.CharField(required=False, allow_blank=False, max_length=64)


class AnswerSerializer(serializers.Serializer):
    """Answers keyed by question public id: {"q_abc123": "text"}.

    Declared as a free-form object because the keys are the event's question ids,
    which are not known until the event is.
    """

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError("Send the answers as an object keyed by question id.")
        return {str(key): value for key, value in data.items()}


class ProjectAnswerSerializer(serializers.Serializer):
    """One custom question answer as the project page shows it."""

    question = serializers.CharField(help_text="Question public id.")
    prompt = serializers.CharField()
    is_public = serializers.BooleanField(
        help_text="Public answers appear on the gallery page; private ones are asked of the "
                  "team and shown to judges only."
    )
    value = serializers.CharField(allow_null=True)


class ProjectImageSerializer(serializers.Serializer):
    position = serializers.IntegerField(help_text="One-based slot in the gallery.")
    caption = serializers.CharField(allow_blank=True)
    url = serializers.CharField()


class ProjectRevisionSerializer(serializers.Serializer):
    """A submitted revision. ``receipt`` is a digest prefix, not a signature."""

    number = serializers.IntegerField()
    created_at = serializers.DateTimeField(help_text="Server timestamp of the submission.")
    receipt = serializers.CharField(
        help_text="First 8 characters of the revision digest. It is an internal checksum "
                  "prefix and is not proof of external authorship."
    )


class ProjectImageCreatedSerializer(serializers.Serializer):
    project = serializers.CharField()
    position = serializers.IntegerField()
    caption = serializers.CharField(allow_blank=True)
    url = serializers.CharField()


class ProjectImageDeletedSerializer(serializers.Serializer):
    deleted = serializers.IntegerField(help_text="Position that was freed.")


class ImageUploadSerializer(serializers.Serializer):
    """multipart/form-data body of an image upload."""

    image = serializers.FileField(
        help_text="jpeg, png, webp or gif, at most 5 MB, at most six per project. Verified "
                  "by decoding it, not by trusting the extension."
    )
    caption = serializers.CharField(required=False, allow_blank=True, max_length=300)


def event_or_404(slug: str) -> Event:
    event = get_event_by_slug(slug)
    if event is None:
        raise ApiError("event_not_found", f"No event with slug {slug!r}.", status_code=404)
    return event


def project_or_404(slug: str, public_id: str, user) -> Project:
    """A project of this event the caller may read, else 404.

    Answering 404 for a project the caller may not see keeps drafts from being
    probed: there is no difference between "no such project" and "not yours".
    """
    event = event_or_404(slug)
    project = visible_project_by_id(user, event, public_id)
    if project is None:
        raise ApiError("project_not_found", f"No project {public_id!r} in {event.name}.",
                       status_code=404)
    return project


class PublicGallery(GenericAPIView):
    """``GET /api/v1/projects``: the public gallery as JSON.

    Anonymous-friendly on purpose and scoped exactly like the HTML gallery, so the
    two can never disagree about what is public.
    """

    permission_classes = [AllowAny]
    pagination_class = VerdictPagination

    @extend_schema(
        operation_id="gallery",
        summary="The public project gallery (submitted projects only).",
        description="Filters: q (title, summary or description), event (slug), track "
                    "(public id), tag (exact, lowercased), sort=title|newest. Page one is "
                    "sorted by title, which is what the acceptance checker reads.",
        parameters=[
            OpenApiParameter("q", str, OpenApiParameter.QUERY, required=False,
                             description="Free text over title, summary and description."),
            OpenApiParameter("event", str, OpenApiParameter.QUERY, required=False,
                             description="Restrict to one event slug."),
            OpenApiParameter("track", str, OpenApiParameter.QUERY, required=False,
                             description="Track public id."),
            OpenApiParameter("tag", str, OpenApiParameter.QUERY, required=False,
                             description="Exact tech tag, lowercased."),
            OpenApiParameter("sort", str, OpenApiParameter.QUERY, required=False,
                             description="'title' (default) or 'newest'."),
        ],
        responses={200: GalleryProjectSerializer(many=True), **error_responses(404)},
        tags=TAGS,
    )
    def get(self, request):
        params = request.query_params
        event_slug = (params.get("event") or "").strip()
        event = None
        if event_slug:
            event = get_event_by_slug(event_slug)
            if event is None:
                raise ApiError("event_not_found", f"No event with slug {event_slug!r}.",
                               status_code=404)
        sort = (params.get("sort") or "title").strip()
        if sort not in SORTS:
            raise ApiError("invalid", "sort must be title or newest.",
                           fields={"sort": ["Use title or newest."]})
        projects = filter_gallery(
            public_projects(event),
            q=params.get("q", ""), track=params.get("track", ""),
            tag=params.get("tag", ""), sort=sort,
        )
        page = self.paginate_queryset(projects)
        return self.get_paginated_response(GalleryProjectSerializer(page, many=True).data)


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
        summary="List the event's submitted projects.",
        description="Public where the event's gallery is public; otherwise 404, so a private "
                    "event is not discoverable through this route either. Drafts are never "
                    "listed.",
        parameters=[
            OpenApiParameter("q", str, OpenApiParameter.QUERY, required=False,
                             description="Free text filter."),
            OpenApiParameter("track", str, OpenApiParameter.QUERY, required=False,
                             description="Track public id."),
            OpenApiParameter("tag", str, OpenApiParameter.QUERY, required=False,
                             description="Exact tech tag."),
        ],
        responses={200: ProjectSerializer(many=True), **error_responses(404, 429)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        event = event_or_404(slug)
        if not event.gallery_public and not is_organizer(request.user, event):
            raise ApiError("event_not_found", f"No event with slug {slug!r}.", status_code=404)
        projects = filter_gallery(public_projects(event),
                                  q=request.query_params.get("q", ""),
                                  track=request.query_params.get("track", ""),
                                  tag=request.query_params.get("tag", ""))
        page = self.paginate_queryset(projects)
        return self.get_paginated_response(ProjectSerializer(page, many=True).data)

    @extend_schema(
        operation_id="event_project_create",
        summary="Create your team's draft project.",
        description="Window closed answers 403 window_closed before any validation; "
                    "no team 403 not_a_participant; a second active project 409 "
                    "team_has_project.",
        request=ProjectWriteSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={201: ProjectSerializer,
                   **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        serializer = ProjectWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = services.create_project(request.user, event_or_404(slug),
                                         dict(serializer.validated_data))
        return Response(ProjectSerializer(project).data, status=201)


class ProjectDetail(GenericAPIView):
    """``GET|PATCH /api/v1/events/{slug}/projects/{id}``.

    GET is anonymous-friendly, like the gallery and the project page: a
    submitted project in a public event is public, whoever asks. PATCH is not.
    """

    serializer_class = ProjectDetailSerializer

    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(
        operation_id="event_project_detail",
        summary="Read one project as the caller is allowed to see it.",
        description="A submitted project in a public event is public. Private custom answers "
                    "and the revision list appear only for the team, organizers and assigned "
                    "judges; can_edit and can_withdraw say which actions this caller has, so "
                    "a client never has to guess.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
        ],
        responses={200: ProjectDetailSerializer, **error_responses(404)},
        tags=TAGS,
    )
    def get(self, request, slug: str, public_id: str):
        project = project_or_404(slug, public_id, request.user)
        return Response(ProjectDetailSerializer(project, context={"viewer": request.user}).data)

    @extend_schema(
        operation_id="event_project_update",
        summary="Edit your team's project.",
        description="Refused with 403 window_closed once submissions close, 409 stale_edit "
                    "when base_updated_at does not match the stored version, and 400 "
                    "cross_event for a track from another event. Editing a submitted project "
                    "writes a new revision immediately.",
        request=ProjectPatchSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
        ],
        responses={200: ProjectDetailSerializer,
                   **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def patch(self, request, slug: str, public_id: str):
        event = event_or_404(slug)
        project = project_or_404(slug, public_id, request.user)
        serializer = ProjectPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = services.update_project(request.user, event, project,
                                          dict(serializer.validated_data))
        return Response(ProjectDetailSerializer(updated, context={"viewer": request.user}).data)


class ProjectSubmit(GenericAPIView):
    """``POST /api/v1/events/{slug}/projects/{id}/submit``."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_project_submit",
        summary="Submit your team's project.",
        description="Requires a title, summary, track, description, repository link and an "
                    "answer to every required question; anything missing comes back under "
                    "error.fields. The response carries the new revision and its receipt.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
        ],
        responses={200: ProjectDetailSerializer,
                   **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request, slug: str, public_id: str):
        project = project_or_404(slug, public_id, request.user)
        submitted = services.submit_project(request.user, project)
        return Response(ProjectDetailSerializer(submitted, context={"viewer": request.user}).data)


class ProjectWithdraw(GenericAPIView):
    """``POST /api/v1/events/{slug}/projects/{id}/withdraw`` (team owner, window open)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_project_withdraw",
        summary="Withdraw your team's submission while the window is open.",
        description="Team owner only. A withdrawn project keeps its record and can be "
                    "resubmitted while the window is still open.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
        ],
        responses={200: ProjectDetailSerializer,
                   **error_responses(401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request, slug: str, public_id: str):
        project = project_or_404(slug, public_id, request.user)
        withdrawn = services.withdraw_project(request.user, project)
        return Response(ProjectDetailSerializer(withdrawn, context={"viewer": request.user}).data)


class ProjectDisqualify(GenericAPIView):
    """``POST /api/v1/events/{slug}/projects/{id}/disqualify`` (organizer, any time)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_project_disqualify",
        summary="Disqualify a project (organizer only, with a reason).",
        description="Unlike every other project write this one is not bound by the "
                    "submission window: an organizer has to be able to stand a project down "
                    "after the close. The reason is stored and audited.",
        request=DisqualifySerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
        ],
        responses={200: ProjectDetailSerializer,
                   **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request, slug: str, public_id: str):
        project = project_or_404(slug, public_id, request.user)
        serializer = DisqualifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = services.disqualify_project(
            request.user, project, serializer.validated_data["reason"],
            expected_digest=serializer.validated_data.get("expected_digest"),
        )
        return Response(ProjectDetailSerializer(updated, context={"viewer": request.user}).data)


class ProjectImageCreate(GenericAPIView):
    """``POST /api/v1/events/{slug}/projects/{id}/images`` (multipart)."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        operation_id="event_project_images",
        summary="Attach a gallery image (jpeg, png, webp or gif, up to 5 MB, six per project).",
        description="The file is verified with Pillow rather than trusted from its "
                    "extension, and stored under a random name.",
        request={"multipart/form-data": ImageUploadSerializer},
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
        ],
        responses={201: ProjectImageCreatedSerializer,
                   **error_responses(400, 401, 403, 404, 409, 413, 415)},
        tags=TAGS,
    )
    def post(self, request, slug: str, public_id: str):
        project = project_or_404(slug, public_id, request.user)
        image = services.add_image(request.user, project, request.FILES.get("image"),
                                   request.data.get("caption", ""))
        return Response({
            "project": project.public_id,
            "position": image.position,
            "caption": image.caption,
            "url": image.image.url,
        }, status=201)


class ProjectImageDetail(GenericAPIView):
    """``DELETE /api/v1/events/{slug}/projects/{id}/images/{n}`` where n is the position."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_project_image_detail",
        summary="Remove one gallery image by its position in the editor.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
            OpenApiParameter("position", int, OpenApiParameter.PATH,
                             description="One-based image slot, as shown in the editor."),
        ],
        responses={200: ProjectImageDeletedSerializer,
                   **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def delete(self, request, slug: str, public_id: str, position: int):
        project = project_or_404(slug, public_id, request.user)
        services.delete_image(request.user, project, position)
        return Response({"deleted": position})


class ProjectAnswers(GenericAPIView):
    """``PUT /api/v1/events/{slug}/projects/{id}/answers``."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_project_answers",
        summary="Save the answers to the event's custom questions.",
        description="Body is {question_public_id: value}. A question from another event is "
                    "400 cross_event. Saving on a submitted project writes a new revision.",
        request=AnswerSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
        ],
        responses={200: ProjectDetailSerializer,
                   **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def put(self, request, slug: str, public_id: str):
        project = project_or_404(slug, public_id, request.user)
        serializer = AnswerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = services.save_answers(request.user, project, serializer.validated_data)
        return Response(ProjectDetailSerializer(updated, context={"viewer": request.user}).data)
