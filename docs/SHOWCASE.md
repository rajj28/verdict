# Calibration showcase (synthetic event `showcase`)

A third seeded event whose truth the portal knows and the database does not. It
exists for one reason: score normalization is easy to claim and hard to see, so
here an organizer can open a page, watch the engine recover planted judge habits
and a planted true order, and rehearse the whole publish flow without touching
`sample-hack-2026` or the live demo event.

| | |
| --- | --- |
| Slug | `showcase` |
| Event name | Calibration showcase (synthetic) |
| Provenance `source_id` | `evt_showcase` |
| Seeded by | `core.bootstrap.bootstrap()` when `DEMO_MODE=1`, and `python manage.py seed_showcase` |
| Calibration page | `/manage/showcase/calibration` (organizer only) |
| Generator | `src/core/showcase.py` (pure, standard library only) |
| Proof of the numbers below | `tests/test_showcase.py` |

Judging is closed, nothing is published, feedback is unreleased. Organizer:
`organizer@verdict.local` (the seeded host) or any admin.

## The generator, exactly

`SHOWCASE_SEED = 20260120`, one `random.Random(seed)`, no clock, no dict-order or
hash-seed dependence. `build()` returns a fixtures-shaped dict that goes through
`interop.importer.import_fixture` like any other fixture, so every data-model
rule applies; `truth()` returns the planted values the page compares against.

1. **Projects.** 24. True quality is the ladder `1.80, 1.91, … 4.40`
   (`(4.4 - 1.8) / 23` per step, stored to 2 dp), and the ladder is shuffled onto
   the 24 project slots, so the strongest project is not the first one listed.
   One team and one member per project (the model allows only one active project
   per team), two tracks, short neutral titles and summaries.
2. **Judges.** 12. The first two are harsh with a planted offset of exactly
   **-1.0** rubric points, the next two are generous at exactly **+1.0**, and the
   remaining eight are neutral with an offset drawn from `N(0, 0.15)`. A judge's
   name (`Showcase Judge 07`) and email (`showcase.judge07@example.org`) never
   reveal which bucket that judge is in.
3. **Routing, and why it is unfair on purpose.** Every project gets 3 reviews
   and every judge 6, so 72 reviews both ways, with no judge reviewing a project
   twice. The two harsh judges cover the **eight strongest** projects, the two
   generous judges the **eight middling** ones, and the eight neutral judges take
   the remaining capacity, dealt in tiers. Raw means are therefore dragged down
   at the top of the table and pushed up in the middle **by construction**. A
   method that only recovers the truth on an evenly-distributed assignment has
   not been shown to work at all.
4. **Scores.** Per criterion,
   `clamp(round(quality + offset + N(0, 0.5)), 1, 5)` on the same three criteria
   as `fixtures.json` (`functionality`, `quality`, `innovation`, equal weight,
   integers 1-5). Rubric point *p* is therefore 25 points on the 0-100 scale the
   engine uses, which is how the page makes the two offset columns comparable.
5. **Rubric λ.** The event keeps `shrinkage_lambda = NULL`, so the official
   procedure runs: λ is chosen by the predeclared 5-fold cross-validation at
   preview time. The page shows whatever λ that selects.

The design is checked, not hoped for: one connected judge-project graph
(`results.engine.components`), exactly 3 reviews per project and 6 per judge, no
repeated judge-project pair.

## What the page shows on a fresh seed

Measured by seeding into an empty database and reading `/manage/showcase/calibration`
with `seed_showcase --reset`. The page recomputes all of it on every request from
the event's current included reviews, so these are the numbers for a fresh
seed, and they move if an organizer excludes a review.

**Shape.** 24 projects, 24 teams, 24 participants, 12 judges, 2 tracks, 2 prizes,
72 reviews, 216 criterion scores, 1 connected component, 0 excluded reviews.
**λ = 0.5**, chosen by 5-fold cross-validation (`lambda_source: auto`).

**1. Judge habits** (planted vs estimated, both on the 0-100 scale, sorted by
planted offset). Correlation between planted and estimated: **Spearman rho 0.982**.

| Judge | Planted habit | Planted | Estimated | Error |
| --- | --- | ---: | ---: | ---: |
| Showcase Judge 01 | harsh | -25.00 | -25.57 | 0.57 |
| Showcase Judge 02 | harsh | -25.00 | -19.38 | 5.62 |
| Showcase Judge 06 | neutral | -6.34 | -9.20 | 2.86 |
| Showcase Judge 12 | neutral | -3.23 | -6.24 | 3.01 |
| Showcase Judge 07 | neutral | -1.95 | +2.81 | 4.75 |
| Showcase Judge 11 | neutral | -0.19 | -1.96 | 1.77 |
| Showcase Judge 08 | neutral | +4.20 | +4.48 | 0.29 |
| Showcase Judge 09 | neutral | +4.34 | +5.79 | 1.45 |
| Showcase Judge 05 | neutral | +5.12 | +5.27 | 0.15 |
| Showcase Judge 10 | neutral | +5.21 | +6.27 | 1.06 |
| Showcase Judge 03 | generous | +25.00 | +16.24 | 8.76 |
| Showcase Judge 04 | generous | +25.00 | +21.48 | 3.52 |

All four planted habits come back with the right sign; the worst of them is 8.8
points out of 100 out, which is 0.35 of a rubric point.

**2. Order recovery**, true quality against each ranking, over all 24 projects:

| Ranking | Kendall tau | Spearman rho | 3 or more places from the true rank |
| --- | ---: | ---: | ---: |
| Raw means | 0.669 | 0.788 | 11 |
| Normalized | 0.877 | 0.969 | 3 |

**3. Top 3**

| Place | True order | Raw means | Normalized |
| --- | --- | --- | --- |
| 1 | Beacon Cache (4.40) | Meadow Board | **Beacon Cache** |
| 2 | Wicker Plan (4.29) | Ferry Watch | Ferry Watch |
| 3 | Ferry Watch (4.17) | Thistle Board | Yarrow Feed |

**4. Verdict** (computed on every request; the sentence below is the one this
seed produces, and no outcome is written into the code):

> Normalization put the sign right for 4 of the 4 planted habits and the worst of
> them came back within 8.8 points on the 0-100 scale; judge offsets ordered by
> the truth at Spearman rho 0.98; order recovery against the true quality rose
> from Kendall tau 0.67 on raw means to 0.88 normalized; projects sitting 3 or
> more places from their true rank fell from 11 on raw means to 3 normalized;
> the true winner Beacon Cache moved from rank 4 (tied) on raw means to rank 1
> normalized.

The true winner is 4th on raw means, tied with three others, because both harsh
judges saw it. Normalization puts it first. That single row is the whole
argument, and it is why the routing is the way it is.

## Six-step rehearsal

Sign in as `organizer@verdict.local`. Every write goes through the JSON API, as
everywhere else; the pages only read.

1. **Preview.** `/manage/showcase/results`. The official ranking, the judge
   table, the robustness certificate, the input digest. Nothing is published and
   nothing is public.
2. **Inspect the calibration page.** `/manage/showcase/calibration` (also linked
   from the organizer overview, only for this event). Check the planted habits
   against the estimated offsets, the order recovery against the raw means, and
   read the honesty note. Exclude a review of a harsh judge and come back: the
   numbers move, because that is what excluding a review means.
3. **Try a consequence.** Back on the results page, use *Disqualify* on a project
   or the exclusion control on a review. The consequence preview shows what the
   ranking and the prizes would do *before* you confirm, and a reason is
   required. Confirm or cancel; the audit log records whichever you did.
4. **Publish.** On the Publish tab, add a note (required once a second version
   exists) and publish. On a fresh seed this awards *Best overall* to Beacon
   Cache and *Most useful* to Ferry Watch, with nothing unawarded.
5. **Verify.** Open the publication's decision record from the publication
   history and run Verify: the stored inputs are replayed and the live data is
   re-hashed, and the two verdicts say whether the publication is reproducible
   and whether anything has moved since. Known issue, not specific to this
   event and not caused by it: a fresh publish of any imported event (this one
   and `sample-hack-2026`) currently verifies as `differs` with a last-digit
   float difference in the `normalized` column, because the verifier compares
   unrounded floats. Treat that as a real finding to report, not as a showcase
   defect.
6. **Certificates.** `/manage/showcase/certificates`. 24 participation, 12 judge
   and 2 winner certificates, each with a verification code; issue the signed
   judge participation records from the same page. The public results page now
   shows the ranking and the awards, and never a judge name or offset.

When the rehearsal is over, reset the event (below) so the next person starts
from the same numbers.

## Seeding and resetting

```sh
python manage.py seed_showcase            # idempotent: a second run says so
python manage.py seed_showcase --reset    # delete and re-import from the generator
python manage.py seed_showcase --reset --slug sample-hack-2026   # refused
```

* The event is imported through `interop.importer.import_fixture`, so the
  fixtures-shaped dict obeys exactly the same rules as `fixtures.json`.
* Two prizes are added through `events.services.create_prize` (the importer does
  not carry prizes) so the rehearsal reaches a winner certificate. Judging is
  closed through `events.services.close_judging` so results can be published.
  Both are skipped when they are already in place.
* `--reset` drops the showcase event and everything hanging off it: ballot items
  and signed judge records first, then the projects (which protect the tracks),
  then the event. The synthetic accounts are left in place; they are harmless
  once unreferenced. Any slug other than `showcase` is refused, so this can never
  touch a real event.
* `core.bootstrap.bootstrap()` does the same thing inside `if settings.DEMO_MODE`,
  after the fixture and the demo events, and never in production. A second boot
  finds the event and writes nothing.

## What this does and does not prove

The data is synthetic and the routing is deliberately wrong in a known
direction. A recovery here shows the method works **when its assumptions hold**:
a known truth, a connected judge-project graph, habits roughly constant per
judge, and enough reviews per judge for the offset to be estimated. It is not a
claim that every real event is fixed by normalization, and a failure on another
event is a reason to look at the assumptions, not at the data. The argument and
its limits are in `docs/NORMALIZATION-PROOF.md`, regenerated from the
organizers' `fixtures.json` by `scripts/normalization_proof.py`.
