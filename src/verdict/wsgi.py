"""WSGI entry point (gunicorn runs with --pythonpath src)."""
import os
import sys
from pathlib import Path

SRC_DIR = str(Path(__file__).resolve().parent.parent)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from django.core.wsgi import get_wsgi_application  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "verdict.settings")

application = get_wsgi_application()
