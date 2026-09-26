"""Template context every page needs (brand, demo mode, current user role)."""
from django.conf import settings


def portal(request):
    return {
        "DEMO_MODE": settings.DEMO_MODE,
        "PORTAL_NAME": "VERDICT",
    }
