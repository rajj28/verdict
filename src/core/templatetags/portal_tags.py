"""Presentation helpers shared by all pages (formatting only, no business logic)."""
import re
from datetime import timezone as datetime_timezone

from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def utc(value):
    """Render a datetime in UTC for receipts and tables (server time is UTC)."""
    if not value:
        return ""
    return timezone.localtime(value, datetime_timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@register.filter
def short(value, length=120):
    text = (value or "").strip()
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


@register.filter
def initials(value):
    """Up to two letters for the .project-thumb placeholder of a title-less card.

    Most projects have no thumbnail, and an empty grey box reads as a broken
    image, so the card falls back to the initials of the name.
    """
    words = [word for word in re.split(r"[\s\-_/]+", (value or "").strip()) if word]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][:1] + words[1][:1]).upper()
