"""Single source of truth for "now" so tests can freeze or advance time."""
from django.utils import timezone


def now():
    """Current time as an aware datetime; tests patch this, never timezone.now()."""
    return timezone.now()
