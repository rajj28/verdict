"""Authenticated, paginated read API for private offline messages."""
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated

from core import outbox_policy
from core.errors import ApiError
from core.models import OutboxMessage
from core.pagination import VerdictPagination
from events.policy import visible_events


class OutboxMessageSerializer(serializers.ModelSerializer):
    event = serializers.CharField(source="event.slug", allow_null=True, default=None, read_only=True)

    class Meta:
        model = OutboxMessage
        fields = ("public_id", "event", "created_at", "recipients", "subject", "body", "from_email")
        read_only_fields = fields


@method_decorator(never_cache, name="dispatch")
class OutboxListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OutboxMessageSerializer
    pagination_class = VerdictPagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return OutboxMessage.objects.none()
        event = None
        if "slug" in self.kwargs:
            event = get_object_or_404(visible_events(self.request.user), slug=self.kwargs["slug"])
        if not outbox_policy.can_view_outbox(self.request.user, event):
            raise ApiError("forbidden", "Only an owning organizer or administrator can read this outbox.",
                           status_code=403)
        return outbox_policy.visible_outbox(self.request.user, event)


class EventOutboxView(OutboxListView):
    @extend_schema(
        operation_id="event_outbox_list", tags=["Outbox"],
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH)],
        summary="Read an event's private offline outbox (organizers and admins).",
        description="Messages are queued for organizer delivery, not sent by SMTP. Bodies may contain "
                    "voting capabilities. Paginated; never available to anonymous users or other events' organizers.",
        responses={200: OutboxMessageSerializer(many=True),
                   401: OpenApiResponse(description="Authentication required."),
                   403: OpenApiResponse(description="Not an organizer of this event."),
                   404: OpenApiResponse(description="Event not found.")},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class AdminOutboxView(OutboxListView):
    @extend_schema(
        operation_id="admin_outbox_list", tags=["Outbox"],
        summary="Read the platform's private offline outbox (admins only).",
        description="Includes event messages and unscoped messages; paginated with a maximum page size of 100.",
        responses={200: OutboxMessageSerializer(many=True),
                   401: OpenApiResponse(description="Authentication required."),
                   403: OpenApiResponse(description="Platform administrator required.")},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
