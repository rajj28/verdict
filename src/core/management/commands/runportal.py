"""Container entry point: wait for the database, migrate, seed, then exec gunicorn.

``os.execvp`` replaces this process, so gunicorn becomes PID 1 of the command and
receives every signal directly (BUILD-SEC section 8).
"""
import os
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

DB_WAIT_SECONDS = 60
DB_POLL_SECONDS = 1.5
BIND = os.environ.get("BIND", "0.0.0.0:8080")
WORKERS = os.environ.get("WEB_CONCURRENCY", "3")
# No query string and no referrer: invite and reset tokens travel in the query
# string and must never reach the log (BUILD-SEC section 16).
ACCESS_LOG_FORMAT = '%(h)s %(t)s "%(m)s %(U)s" %(s)s %(b)s %(L)s'


def wait_for_database(attempts: int = int(DB_WAIT_SECONDS / DB_POLL_SECONDS)) -> None:
    """Poll until Postgres answers, so boot order in compose cannot race us."""
    for attempt in range(1, attempts + 1):
        try:
            connections["default"].cursor().execute("SELECT 1")
            return
        except Exception as error:  # noqa: BLE001 - any connection error is retried
            if attempt == attempts:
                raise CommandError(
                    f"Database unreachable after {DB_WAIT_SECONDS}s: {error}"
                ) from error
            time.sleep(DB_POLL_SECONDS)
    raise CommandError("Database unreachable.")


class Command(BaseCommand):
    help = "Wait for the database, migrate, seed and serve with gunicorn."

    def handle(self, *args, **options):
        wait_for_database()
        call_command("migrate", interactive=False, verbosity=1)
        call_command("bootstrap")
        argv = [
            "gunicorn",
            "--pythonpath", "src",
            "--bind", BIND,
            "--workers", WORKERS,
            "--access-logfile", "-",
            "--access-logformat", ACCESS_LOG_FORMAT,
            "verdict.wsgi",
        ]
        self.stdout.write(f"starting {' '.join(argv)}")
        os.execvp("gunicorn", argv)
