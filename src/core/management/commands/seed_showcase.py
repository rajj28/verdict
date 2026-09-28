"""Seed the synthetic calibration showcase. ``python manage.py seed_showcase``."""
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from core.bootstrap import ADMIN_EMAIL, ORGANIZER_EMAIL, seed_showcase_event
from core.showcase import SHOWCASE_SLUG


def _actor() -> User:
    """The administrator the import is attributed to, or a refusal."""
    actor = (
        User.objects.filter(email=ADMIN_EMAIL).first()
        or User.objects.filter(is_admin=True, is_active=True).order_by("id").first()
    )
    if actor is None or not actor.is_admin:
        raise CommandError(
            "No administrator account found. Run 'python manage.py bootstrap' first."
        )
    return actor


class Command(BaseCommand):
    help = (
        "Seed the synthetic calibration showcase event: 24 projects with a known true "
        "quality and 12 judges with planted habits, judging closed, nothing published "
        "(idempotent)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--slug",
            default=SHOWCASE_SLUG,
            help=f"Event slug to seed. Only {SHOWCASE_SLUG!r} is accepted.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help=f"Delete the {SHOWCASE_SLUG!r} event and re-import it from the generator.",
        )

    def handle(self, *args, **options):
        slug = options["slug"]
        organizer = User.objects.filter(email=ORGANIZER_EMAIL).first()
        if organizer is None:
            raise CommandError(
                f"No {ORGANIZER_EMAIL} account found. Run 'python manage.py bootstrap' first."
            )
        try:
            report = seed_showcase_event(
                admin=_actor(), organizer=organizer, reset=bool(options["reset"]), slug=slug,
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        if not report.changed:
            self.stdout.write(
                f"Nothing to do: the calibration showcase ({report.slug}) is already seeded."
            )
            return
        if report.imported:
            counts = report.counts
            self.stdout.write(
                f"Imported the calibration showcase as {report.slug}: "
                f"{counts.get('projects', 0)} projects, {counts.get('reviews', 0)} reviews, "
                f"{counts.get('judges', 0)} judges, {counts.get('criterion_scores', 0)} "
                "criterion scores."
            )
        else:
            self.stdout.write(f"Re-seeded the calibration showcase {report.slug}.")
        if report.prizes_created:
            self.stdout.write(f"Created {report.prizes_created} prize(s) for the rehearsal.")
        if report.judging_closed:
            self.stdout.write("Judging closed; results can be published but nothing is published.")
        if report.organizer_added:
            self.stdout.write(f"{organizer.display_name} is an organizer of {report.slug}.")
        self.stdout.write(
            f"Open /manage/{report.slug}/calibration to compare the planted truth with "
            "the recovered one."
        )
