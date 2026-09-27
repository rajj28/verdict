"""Admin and event-organizer access to the disposable integrity probe."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.errors import ApiError
from core.probe import run_probe
from events.policy import is_organizer, visible_events


class IntegrityProbeView(APIView):
    permission_classes = [IsAuthenticated]

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
