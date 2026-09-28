# The click-and-play tour

With `DEMO_MODE=1`, the home page offers a five-minute rehearsal. Starting it
imports a synthetic calibration event into a private event slug tied to the
browser session. The organizer, judge and participant roles are separate
unusable-password accounts under `@tour.verdict.local`; switching roles uses
the normal Django session and never grants access to another session's event.

The panel is server-described and keyboard-friendly. It walks through the
closed submission deadline, the public gallery, judge isolation, open reviews,
progress, normalization, consequence previews, publication and certificates.
Writes continue through the ordinary JSON APIs, so the rehearsal exercises the
same backend permissions and audit trail as a live event.

Tour events are hidden from public browsing and are removed after six hours.
There can be at most 60 live sandboxes, and start/reset requests are throttled
per client address. Operators can prune them with
`python manage.py prune_tour_sandboxes` (the `--older-than-hours` option is
accepted for scheduled jobs). The feature is completely absent when
`DEMO_MODE=0`: tour pages and endpoints return 404 and the home card is hidden.
