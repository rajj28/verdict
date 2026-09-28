"""Admin and event-organizer access to the disposable integrity probe."""
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.errors import ApiError
from core.probe import run_probe
from events.policy import is_organizer, visible_events

from core.schema import error_responses


class IntegrityProbeSerializer(serializers.Serializer):
    """One attack the portal refused, and the rule that refused it."""

    key = serializers.CharField(help_text="Stable name of this attack case.")
    area = serializers.CharField(
        help_text="JUDGING, SUBMISSIONS, PROJECTS, MEDIA, EVENT ISOLATION, EXPORTS, AUTH, "
                  "VOTING, CERTIFICATES, SIGNED RECORDS or WEBHOOKS."
    )
    description = serializers.CharField(allow_blank=True)
    method = serializers.CharField()
    path = serializers.CharField(
        help_text="The URL attacked. Disposable invite capabilities are shown as "
                  "[temporary] so the report carries no live token."
    )
    actor = serializers.CharField(help_text="Who made the request, as the probe drove it.")
    expected = serializers.CharField(help_text="The status codes that would mean 'refused'.")
    actual = serializers.CharField(allow_blank=True)
    status_code = serializers.IntegerField()
    passed = serializers.BooleanField(
        help_text="True when the response was the expected refusal, or when the leak the "
                  "case was checking for was absent."
    )
    reason = serializers.CharField(
        allow_blank=True, help_text="The rule this case defends, or why it failed."
    )


class IntegrityProbeRequestSerializer(serializers.Serializer):
    event = serializers.CharField(
        required=False, allow_blank=True,
        help_text="Event slug to scope the probe to. A platform admin may omit it to probe "
                  "the whole stack; an organizer must name an event they organize.",
    )


class IntegrityProbeReportSerializer(serializers.Serializer):
    """The report. Every fixture row the probe created is rolled back before it returns."""

    ok = serializers.BooleanField(help_text="True only when every case was refused.")
    passed = serializers.IntegerField()
    total = serializers.IntegerField()
    summary = serializers.CharField(help_text="For example '31/31 attacks refused'.")
    event = serializers.CharField(allow_blank=True, help_text="Scoped event slug, if any.")
    extensions = serializers.ListField(
        child=serializers.CharField(),
        help_text="Registry extension areas the probe exercises: voting and publication.",
    )
    cases = IntegrityProbeSerializer(many=True)


class IntegrityProbeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="integrity_probe",
        summary="Run the adversarial integrity probe (admin, or an event's organizer).",
        description="Sends real rollback-only adversarial requests through the real URL stack, "
                    "then discards every fixture row it created in one transaction. Nothing "
                    "the probe writes is ever committed. A platform admin may probe the whole "
                    "stack or name an event; an organizer must name an event they organize, "
                    "and gets 403 choose_an_event_you_organize otherwise. The run is audited "
                    "with every case in the audit record.",
        request=IntegrityProbeRequestSerializer,
        tags=["core"],
        responses={200: IntegrityProbeReportSerializer, **error_responses(400, 401, 403, 404)},
    )
    def post(self, request):
        user = request.user
        if not hasattr(request.data, "get"):
            raise ApiError("invalid", "Send the probe scope as an object.")
        event_slug = str(request.data.get("event", "") or "").strip()
        event = None
        if not getattr(user, "is_admin", False):
            if not event_slug:
                raise ApiError("forbidden", "Choose an event you organize.", status_code=403)
            event = visible_events(user).filter(slug=event_slug).first()
            if event is None:
                raise ApiError("event_not_found", "That event does not exist.", status_code=404)
            if not is_organizer(user, event):
                raise ApiError("forbidden", "Only this event's organizer can run its probe.",
                               status_code=403)
        elif event_slug:
            event = visible_events(user).filter(slug=event_slug).first()
            if event is None:
                raise ApiError("event_not_found", "That event does not exist.", status_code=404)
        return Response(run_probe(actor=user, event=event))
