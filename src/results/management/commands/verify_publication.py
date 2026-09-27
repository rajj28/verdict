"""``manage.py verify_publication pub_id``

Recompute results from a stored publication's inputs, compare rows and the
live-database digest, and print a clear verdict (BUILD-SPEC section 16).

Usage::

    python manage.py verify_publication pub_xxxxxxxxxxx
    python manage.py verify_publication pub_xxxxxxxxxxx --event sample-hack-2026
"""
from django.core.management.base import BaseCommand, CommandError

from results.models import ResultPublication
from results.services import verify_publication


class Command(BaseCommand):
    help = "Verify a ResultPublication: recompute from stored inputs and compare."

    def add_arguments(self, parser):
        parser.add_argument(
            "pub_id",
            help="The public_id of the ResultPublication to verify.",
        )
        parser.add_argument(
            "--event",
            dest="event_slug",
            default=None,
            help="Restrict the lookup to a specific event slug (optional).",
        )

    def handle(self, *args, **options):
        pub_id = options["pub_id"]
        event_slug = options["event_slug"]

        qs = ResultPublication.objects.select_related("event", "published_by")
        if event_slug:
            qs = qs.filter(event__slug=event_slug)
        pub = qs.filter(public_id=pub_id).first()
        if pub is None:
            raise CommandError(
                f"No publication with public_id={pub_id!r}"
                + (f" in event {event_slug!r}" if event_slug else "") + "."
            )

        self.stdout.write(f"Verifying publication {pub.public_id} …")
        self.stdout.write(f"  Event:        {pub.event.slug}")
        self.stdout.write(f"  Method:       {pub.method}")
        self.stdout.write(f"  Published at: {pub.published_at.isoformat()}")
        self.stdout.write(f"  Stored digest: {pub.input_digest}")

        result = verify_publication(pub)

        self.stdout.write("")
        if result["verdict"] == "identical":
            self.stdout.write(self.style.SUCCESS(f"✓ IDENTICAL — {result['detail']}"))
        else:
            self.stdout.write(self.style.ERROR(f"✗ DIFFERS — {result['detail']}"))
            self.stdout.write(f"  rows_match:    {result['rows_match']}")
            self.stdout.write(f"  digest_match:  {result['digest_match']}")
            self.stdout.write(f"  stored digest: {result['stored_digest']}")
            self.stdout.write(f"  live digest:   {result['live_digest']}")
