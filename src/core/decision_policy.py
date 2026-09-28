"""Organizer-only entry point for the Decision Room's private diagnostics."""
from core.errors import ApiError
from events.policy import visible_events
from results.policy import require_organizer


def managed_event(user, slug):
    event = visible_events(user).filter(slug=slug).first()
    if event is None:
        raise ApiError("event_not_found", "This event does not exist.", status_code=404)
    require_organizer(user, event)
    return event
