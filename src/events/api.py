"""Events, tracks, prizes, custom questions and per-event roles.

DRF serializers and viewsets. Thin: validate, delegate to services, return the envelope.
"""
from core.errors import ApiError
from drf_spectacular.utils import OpenApiParameter, extend_schema
from events import services
from events.models import CustomQuestion, Event, EventRole, JudgingMode, Prize, QuestionKind, RankingMethod, Role, Track
from events.policy import get_event_by_slug, submission_window_open
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.schema import error_responses

TAGS = ["events"]


class EventSerializer(serializers.ModelSerializer):
    phase = serializers.SerializerMethodField()
    submission_window_open = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            "slug", "name", "tagline", "description", "submissions_open_at", "submissions_close_at",
            "judging_open_at", "judging_close_at", "max_team_size", "reviews_per_project",
            "judging_mode", "ranking_method", "shrinkage_lambda", "gallery_public", "phase",
            "submission_window_open", "pairwise_min_comparisons",
        ]

    def get_phase(self, event: Event) -> str:
        if event.result_publications.exists():
            return "results_published"
        if services._submission_window_open(event):
            return "submissions_open"
        if event.judging_open_time() is not None:
            return "judging"
        return "upcoming"

    def get_submission_window_open(self, event: Event) -> bool:
        return submission_window_open(event)


class EventWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160)
    tagline = serializers.CharField(max_length=200, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    submissions_open_at = serializers.DateTimeField(required=False, allow_null=True)
    submissions_close_at = serializers.DateTimeField()
    judging_open_at = serializers.DateTimeField(required=False, allow_null=True)
    judging_close_at = serializers.DateTimeField(required=False, allow_null=True)
    max_team_size = serializers.IntegerField(required=False, min_value=1, max_value=10)
    reviews_per_project = serializers.IntegerField(required=False, min_value=1)
    pairwise_min_comparisons = serializers.IntegerField(required=False, min_value=1, max_value=32767)
    judging_mode = serializers.ChoiceField(choices=JudgingMode.choices, required=False)
    ranking_method = serializers.ChoiceField(choices=RankingMethod.choices, required=False)
    shrinkage_lambda = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    gallery_public = serializers.BooleanField(required=False)
    one_prize_per_team = serializers.BooleanField(required=False)


class EventPatchSerializer(EventWriteSerializer):
    name = serializers.CharField(max_length=160, required=False)
    submissions_close_at = serializers.DateTimeField(required=False)


class TrackSerializer(serializers.ModelSerializer):
    class Meta:
        model = Track
        fields = ["public_id", "name", "description", "position"]


class TrackWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    description = serializers.CharField(required=False, allow_blank=True)
    position = serializers.IntegerField(required=False, min_value=0)


class PrizeSerializer(serializers.ModelSerializer):
    track = serializers.SlugRelatedField(slug_field="public_id", read_only=True)

    class Meta:
        model = Prize
        fields = [
            "public_id", "name", "description", "value", "track", "position",
            "scope", "places", "eligibility_note",
        ]


class PrizeWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(required=False, allow_blank=True)
    value = serializers.CharField(required=False, allow_blank=True, max_length=120)
    track = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    position = serializers.IntegerField(required=False, min_value=0)
    places = serializers.IntegerField(required=False, min_value=1)
    eligibility_note = serializers.CharField(required=False, allow_blank=True, max_length=300)


class QuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomQuestion
        fields = [
            "public_id", "prompt", "help_text", "kind", "choices", "required", "is_public",
            "is_active", "position",
        ]


class QuestionWriteSerializer(serializers.Serializer):
    prompt = serializers.CharField(max_length=300)
    help_text = serializers.CharField(required=False, allow_blank=True, max_length=300)
    kind = serializers.ChoiceField(choices=QuestionKind.choices, required=False)
    choices = serializers.ListField(child=serializers.CharField(), required=False)
    required = serializers.BooleanField(required=False)
    is_public = serializers.BooleanField(required=False)
    is_active = serializers.BooleanField(required=False)
    position = serializers.IntegerField(required=False, min_value=0)


class RoleSerializer(serializers.Serializer):
    """An event role. ``user`` is a display name, never an email address."""

    public_id = serializers.CharField()
    user = serializers.CharField()
    role = serializers.CharField(help_text="participant, judge, organizer, host or admin.")


class OrganizerAddSerializer(serializers.Serializer):
    email = serializers.EmailField(help_text="Account to give the organizer role.")


def role_payload(role: EventRole) -> dict:
    return {
        "public_id": role.public_id,
        "user": role.user.display_name or role.user.email.split("@")[0],
        "role": role.role,
    }


def event_or_404(slug: str) -> Event:
    event = get_event_by_slug(slug)
    if event is None:
        raise ApiError("event_not_found", f"No event with slug {slug!r}.", status_code=404)
    return event


def track_or_404(event: Event, public_id: str) -> Track:
    track = Track.objects.filter(event=event, public_id=public_id).first()
    if track is None:
        raise ApiError("track_not_found", f"No track {public_id!r} in {event.name}.", status_code=404)
    return track


def prize_or_404(event: Event, public_id: str) -> Prize:
    prize = Prize.objects.filter(event=event, public_id=public_id).first()
    if prize is None:
        raise ApiError("prize_not_found", f"No prize {public_id!r} in {event.name}.", status_code=404)
    return prize


def question_or_404(event: Event, public_id: str) -> CustomQuestion:
    question = CustomQuestion.objects.filter(event=event, public_id=public_id).first()
    if question is None:
        raise ApiError("question_not_found", f"No question {public_id!r} in {event.name}.", status_code=404)
    return question


class EventListCreate(APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(
        operation_id="events",
        summary="List every event with its current phase.",
        description="Public. A closed or not-yet-open event is still listed, so it is not a "
                    "404 for a reader.",
        responses={200: EventSerializer(many=True), **error_responses(429)},
        tags=TAGS,
    )
    def get(self, request):
        events = Event.objects.prefetch_related("result_publications").order_by("name", "slug")
        return Response(EventSerializer(events, many=True).data)

    @extend_schema(
        operation_id="event_create",
        summary="Create an event; the caller becomes its first organizer.",
        description="Windows are half-open: a submission is accepted at "
                    "submissions_open_at and refused at submissions_close_at.",
        request=EventWriteSerializer,
        responses={201: EventSerializer, **error_responses(400, 401, 403)},
        tags=TAGS,
    )
    def post(self, request):
        serializer = EventWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = services.create_event(request.user, dict(serializer.validated_data))
        return Response(EventSerializer(event).data, status=201)


class EventDetail(APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(
        operation_id="event_detail",
        summary="Read one event, including whether its submission window is open now.",
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: EventSerializer, **error_responses(404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        return Response(EventSerializer(event_or_404(slug)).data)

    @extend_schema(
        operation_id="event_update",
        summary="Change an event's settings, submission window or judging window.",
        description="Organizer only. A moved window is re-checked under lock, so a patch "
                    "that crosses the close instant is a 409, not a silent change.",
        request=EventPatchSerializer,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: EventSerializer, **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def patch(self, request, slug: str):
        serializer = EventPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        event = services.update_event(request.user, event_or_404(slug), dict(serializer.validated_data))
        return Response(EventSerializer(event).data)


class CloseJudging(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_close_judging",
        summary="Close judging now.",
        description="Organizer only. Sets judging_close_at from the server clock, so the "
                    "recheck under lock, not the page, decides whether a submit still "
                    "lands. Audited with the old and the new value.",
        request=None,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: EventSerializer, **error_responses(401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        return Response(EventSerializer(services.close_judging(request.user, event_or_404(slug))).data)


class CloseSubmissions(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_close_submissions",
        summary="Close submissions now.",
        description="Organizer only. Sets submissions_close_at from the server clock. "
                    "Submissions already accepted are unaffected; new and edited ones are "
                    "refused from that instant.",
        request=None,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: EventSerializer, **error_responses(401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        return Response(EventSerializer(services.close_submissions(request.user, event_or_404(slug))).data)


class TrackListCreate(APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(
        operation_id="event_tracks",
        summary="List the event's tracks.",
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: TrackSerializer(many=True), **error_responses(404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        event = event_or_404(slug)
        return Response(TrackSerializer(event.tracks.all(), many=True).data)

    @extend_schema(
        operation_id="event_track_create",
        summary="Create a track (organizer).",
        description="Tracks group submissions for judging; a project sits in at most one.",
        request=TrackWriteSerializer,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={201: TrackSerializer, **error_responses(400, 401, 403, 404)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        serializer = TrackWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        track = services.create_track(request.user, event_or_404(slug), dict(serializer.validated_data))
        return Response(TrackSerializer(track).data, status=201)


class TrackDetail(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_track_update",
        summary="Change a track (organizer).",
        request=TrackWriteSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Public id of this object."),
        ],
        responses={200: TrackSerializer, **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def patch(self, request, slug: str, public_id: str):
        serializer = TrackWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        event = event_or_404(slug)
        track = services.update_track(request.user, event, track_or_404(event, public_id),
                                      dict(serializer.validated_data))
        return Response(TrackSerializer(track).data)

    @extend_schema(
        operation_id="event_track_delete",
        summary="Delete an empty track (organizer).",
        description="Refused with 409 while projects still reference the track, because "
                    "deleting it would silently re-file those submissions.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Public id of this object."),
        ],
        responses={204: None, **error_responses(401, 403, 404, 409)},
        tags=TAGS,
    )
    def delete(self, request, slug: str, public_id: str):
        event = event_or_404(slug)
        services.delete_track(request.user, event, track_or_404(event, public_id))
        return Response(status=204)


class PrizeListCreate(APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(
        operation_id="event_prizes",
        summary="List the event's prizes and the track each is limited to.",
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: PrizeSerializer(many=True), **error_responses(404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        event = event_or_404(slug)
        return Response(PrizeSerializer(event.prizes.select_related("track"), many=True).data)

    @extend_schema(
        operation_id="event_prize_create",
        summary="Create a prize (organizer).",
        request=PrizeWriteSerializer,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={201: PrizeSerializer, **error_responses(400, 401, 403, 404)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        serializer = PrizeWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        prize = services.create_prize(request.user, event_or_404(slug), dict(serializer.validated_data))
        return Response(PrizeSerializer(prize).data, status=201)


class PrizeDetail(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_prize_update",
        summary="Change a prize (organizer).",
        request=PrizeWriteSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Public id of this object."),
        ],
        responses={200: PrizeSerializer, **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def patch(self, request, slug: str, public_id: str):
        serializer = PrizeWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        event = event_or_404(slug)
        prize = services.update_prize(request.user, event, prize_or_404(event, public_id),
                                      dict(serializer.validated_data))
        return Response(PrizeSerializer(prize).data)

    @extend_schema(
        operation_id="event_prize_delete",
        summary="Delete a prize (organizer).",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Public id of this object."),
        ],
        responses={204: None, **error_responses(401, 403, 404, 409)},
        tags=TAGS,
    )
    def delete(self, request, slug: str, public_id: str):
        event = event_or_404(slug)
        services.delete_prize(request.user, event, prize_or_404(event, public_id))
        return Response(status=204)


class QuestionListCreate(APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(
        operation_id="event_questions",
        summary="List the event's custom submission questions.",
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: QuestionSerializer(many=True), **error_responses(404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        event = event_or_404(slug)
        return Response(QuestionSerializer(event.questions.all(), many=True).data)

    @extend_schema(
        operation_id="event_question_create",
        summary="Add a custom submission question (organizer).",
        description="A public question appears on the public project page; a private one is "
                    "asked of the team and shown to judges, not to the gallery.",
        request=QuestionWriteSerializer,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={201: QuestionSerializer, **error_responses(400, 401, 403, 404)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        serializer = QuestionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question = services.create_question(request.user, event_or_404(slug), dict(serializer.validated_data))
        return Response(QuestionSerializer(question).data, status=201)


class QuestionDetail(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_question_update",
        summary="Change a custom question (organizer).",
        request=QuestionWriteSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Public id of this object."),
        ],
        responses={200: QuestionSerializer, **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def patch(self, request, slug: str, public_id: str):
        serializer = QuestionWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        event = event_or_404(slug)
        question = services.update_question(request.user, event, question_or_404(event, public_id),
                                            dict(serializer.validated_data))
        return Response(QuestionSerializer(question).data)

    @extend_schema(
        operation_id="event_question_delete",
        summary="Delete a custom question (organizer).",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Public id of this object."),
        ],
        responses={204: None, **error_responses(401, 403, 404, 409)},
        tags=TAGS,
    )
    def delete(self, request, slug: str, public_id: str):
        event = event_or_404(slug)
        services.delete_question(request.user, event, question_or_404(event, public_id))
        return Response(status=204)


class RegisterParticipant(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_register",
        summary="Join an event as a participant.",
        description="Idempotent: the first call answers 201 and a repeat answers 200 with "
                    "the same role. This is the only step needed before creating a team.",
        request=None,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: RoleSerializer, 201: RoleSerializer, **error_responses(401, 404)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        role, created = services.register_participant(request.user, event_or_404(slug))
        return Response(role_payload(role), status=201 if created else 200)


class OrganizerListCreate(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_organizers",
        summary="List the event's organizers.",
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: RoleSerializer(many=True), **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        event = event_or_404(slug)
        if not services._can_manage(request.user, event):
            raise ApiError("forbidden", "Only organizers of this event can list organizers.", status_code=403)
        roles = EventRole.objects.filter(event=event, role=Role.ORGANIZER).select_related("user")
        return Response([role_payload(role) for role in roles])

    @extend_schema(
        operation_id="event_organizer_add",
        summary="Give an existing account the organizer role in this event.",
        description="Organizer only. The account must already exist; inviting someone new "
                    "goes through the admin reset-link flow. Idempotent.",
        request=OrganizerAddSerializer,
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: RoleSerializer, 201: RoleSerializer,
                   **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        email = (request.data.get("email") or "").strip()
        role, created = services.add_organizer(request.user, event_or_404(slug), email)
        return Response(role_payload(role), status=201 if created else 200)


class OrganizerDetail(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_organizer_remove",
        summary="Remove an organizer's role in this event.",
        description="Organizer only. Refused with 409 when it would leave the event with no "
                    "organizer at all.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Event role public id."),
        ],
        responses={204: None, **error_responses(401, 403, 404, 409)},
        tags=TAGS,
    )
    def delete(self, request, slug: str, public_id: str):
        event = event_or_404(slug)
        role = EventRole.objects.filter(event=event, role=Role.ORGANIZER, public_id=public_id).first()
        if role is None:
            raise ApiError("organizer_not_found", f"No organizer {public_id!r} in {event.name}.",
                           status_code=404)
        services.remove_organizer(request.user, event, role)
        return Response(status=204)
