"""Read-only private outbox pages."""
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from core import outbox_policy
from events.policy import visible_events


def _outbox(request, event=None):
    if not outbox_policy.can_view_outbox(request.user, event):
        raise PermissionDenied("Only an owning organizer or administrator can read this outbox.")
    page = Paginator(outbox_policy.visible_outbox(request.user, event), 50).get_page(request.GET.get("page"))
    return render(request, "core/outbox.html", {
        "event": event,
        "outbox_page": page,
        "api_url": f"/api/v1/events/{event.slug}/outbox" if event else "/api/v1/admin/outbox",
    })


@never_cache
@require_GET
def event_outbox(request, slug: str):
    event = get_object_or_404(visible_events(request.user), slug=slug)
    return _outbox(request, event)


@never_cache
@require_GET
def admin_outbox(request):
    return _outbox(request)
