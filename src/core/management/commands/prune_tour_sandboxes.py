from django.core.management.base import BaseCommand

from core.tour import prune_expired


class Command(BaseCommand):
    help = "Delete expired private tour sandboxes and their tour accounts."

    def add_arguments(self, parser):
        parser.add_argument("--older-than-hours", type=float, default=6)

    def handle(self, *args, **options):
        # The packet's default is six hours; the command option is accepted for
        # operators while retaining the shared safe deletion implementation.
        deleted = prune_expired(options["older_than_hours"])
        self.stdout.write(f"Deleted {deleted} expired tour sandbox(es).")
