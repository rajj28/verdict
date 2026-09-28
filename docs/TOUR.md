# The click-and-play tour

With `DEMO_MODE=1`, the home page offers a five-minute rehearsal. Starting it
imports a synthetic calibration event into a private event slug tied to the
browser session. Every sandbox is a full copy of the calibration showcase:
12 judges, 24 teams and projects, and all 72 planted reviews. Two reviews for
`jdg_sc01` start as drafts so the scoring and publish flow can be rehearsed.

Every sandbox has its own organizer, 12 judge accounts and team-member
accounts, all with unique `@tour.verdict.local` addresses and unusable
passwords. No account is reused across sandboxes or attached to another event.
The tour participant is the member of the first team. Role switching selects
these designated accounts by their fixture role ids and sandbox-specific
organizer/member ids; it never picks an arbitrary account by role.

The panel is server-described and keyboard-friendly. It walks through the
closed submission deadline, the public gallery, judge isolation, open reviews,
progress, normalization, consequence previews, publication and certificates.
The sandbox calibration page computes the real judge-habits, order-recovery,
top-three, verdict and honesty sections from that sandbox's live reviews, with
planted values mapped through fixture ids. Writes continue through the ordinary
JSON APIs, so the rehearsal exercises the same backend permissions and audit
trail as a live event.

Tour events and their accounts are hidden from public browsing and are removed
after six hours. Reset and pruning remove the sandbox-owned accounts with the
event, without deleting any other user or event.
There can be at most 60 live sandboxes, and start/reset requests are throttled
per client address. Operators can prune them with
`python manage.py prune_tour_sandboxes` (the `--older-than-hours` option is
accepted for scheduled jobs). The feature is completely absent when
`DEMO_MODE=0`: tour pages and endpoints return 404 and the home card is hidden.
