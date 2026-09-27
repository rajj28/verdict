"""Run rollback-only adversarial HTTP checks."""
import json

from django.conf import settings
from django.core.management.base import BaseCommand

from core.probe import run_probe


class Command(BaseCommand):
    help = "Run the disposable integrity attack suite against the real Django routes."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json",
                            help="Print the full report as JSON.")
        parser.add_argument("--write", action="store_true",
                            help="Write run.py-style output to attack-report.txt.")

    def handle(self, *args, **options):
        report = run_probe()
        lines = [
            f"{case['area']:<16} {case['description']} ..... "
            f"{'REFUSED' if case['passed'] else 'LEAKED'} {case['actual']} "
            f"(expected {case['expected']})"
            for case in report["cases"]
        ]
        lines.append(f"summary {report['summary']}")
        if options["write"]:
            target = settings.REPO_DIR / "attack-report.txt"
            target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        if options["as_json"]:
            self.stdout.write(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            self.stdout.write("\n".join(lines))
        if not report["ok"]:
            raise SystemExit(1)
