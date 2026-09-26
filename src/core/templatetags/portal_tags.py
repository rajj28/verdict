"""Presentation helpers shared by all pages (formatting only, no business logic)."""
from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def utc(value):
    """Render a datetime in UTC for receipts and tables (server time is UTC)."""
    if not value:
        return ""
    return timezone.localtime(value, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@register.filter
def short(value, length=120):
    text = (value or "").strip()
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"
