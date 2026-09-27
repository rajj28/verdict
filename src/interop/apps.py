"""Schema for the interop app."""
from django.apps import AppConfig


class InteropConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "interop"

    def ready(self):
        from interop import webhooks  # noqa: F401
        from interop.signing import ensure_current_key

        ensure_current_key()
