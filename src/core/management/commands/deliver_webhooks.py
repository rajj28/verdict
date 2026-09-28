"""Run the durable webhook outbox consumer independently of web requests."""
import signal

from django.core.management.base import BaseCommand, CommandError

from interop.webhooks import DeliveryWorker, MAX_WORKERS


class Command(BaseCommand):
    help = "Deliver due webhooks with bounded workers and recover expired leases."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process one bounded due batch and exit.")
        parser.add_argument("--workers", type=int, default=2)
        parser.add_argument("--poll-interval", type=float, default=1.0)

    def handle(self, *args, **options):
        if not 1 <= options["workers"] <= MAX_WORKERS:
            raise CommandError(f"--workers must be between 1 and {MAX_WORKERS}.")
        if not 0.1 <= options["poll_interval"] <= 60:
            raise CommandError("--poll-interval must be between 0.1 and 60 seconds.")
        worker = DeliveryWorker(max_workers=options["workers"])
        prior_handler = signal.signal(signal.SIGTERM, lambda *_: worker.stop())
        try:
            worker.run(once=options["once"], poll_interval=options["poll_interval"])
        except KeyboardInterrupt:
            worker.stop()
        finally:
            signal.signal(signal.SIGTERM, prior_handler)
