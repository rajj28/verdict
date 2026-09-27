"""Schema for the core app."""
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Importing the module registers the bearer security scheme globally.
        from core import schema  # noqa: F401
