"""Thin HTML and JSON adapters for the organizer Decision Room."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.decisions import decision_room
from core.errors import ApiError
from core.schema import error_responses


@login_required
@never_cache
def decision_page(request, slug):
    try:
        report = decision_room(request.user, slug)
    except ApiError as error:
        if error.status_code == 404:
            raise Http404(error.message) from error
        if error.status_code in {401, 403}:
            raise PermissionDenied(error.message) from error
        raise
    return render(request, "manage/decision_room.html", {"room": report})


class DecisionRoomView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["Decision Room"], operation_id="event_decision_room",
                   summary="Organizer-only publication readiness and next actions",
                   responses={200: OpenApiTypes.OBJECT, **error_responses(401, 403, 404)})
    def get(self, request, slug):
        response = Response(decision_room(request.user, slug))
        response["Cache-Control"] = "private, no-store"
        return response
