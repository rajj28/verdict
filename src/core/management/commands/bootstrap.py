"""Idempotent boot seed. ``python manage.py bootstrap``."""
from django.core.management.base import BaseCommand

from core.bootstrap import banner, bootstrap


class Command(BaseCommand):
    help = "Seed the fixture event, demo accounts, demo event and demo tokens (idempotent)."

    def handle(self, *args, **options):
        report = bootstrap()
        self.stdout.write(banner(report))
