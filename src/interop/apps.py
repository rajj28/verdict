"""Schema for the interop app."""
from django.apps import AppConfig


class InteropConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "interop"
