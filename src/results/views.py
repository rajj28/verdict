"""Results HTML views.

Server-rendered pages for organizers and the public. All writes go through
the JSON API; these views only read.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from events.models import Event
from events.policy import get_event_by_slug, is_organizer
from results.models import ResultPublication
from results.policy import require_organizer


def results_public(request, slug: str):
    """Public results page for an event."""
    event = get_object_or_404(Event, slug=slug)
    pub = ResultPublication.objects.filter(event=event).order_by("-published_at").first()
    return render(request, "results/public.html", {"event": event, "publication": pub})
