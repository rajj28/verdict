# Q1: Per-judge review order (counter serial-position effects)

Read `AGENTS.md` and section 2, item 5 of `docs/REAL-WORLD-JUDGING.md`. PostgreSQL
only; use the `DATABASE_URL` you are given.

## Why

Juried competitions show serial-position effects: entries judged later get
different marks (Bruine de Bruin 2005 and 2006; Gavel's author reports order
dependency too). Today every judge's queue is sorted by project title
(`src/judging/policy.py`, `order_by("project__title", ...)`), so the same
projects are always judged first or last by everyone, and any position effect
piles onto the same teams. A per-judge order spreads it out instead.

## Contract

- Each judge sees their assignments in a per-judge order that is deterministic
  and stable across page loads: sort key
  `sha256(f"{event.public_id}:{judge_role.public_id}:{project.public_id}")`. Two
  judges with the same projects get different orders; the same judge always gets
  the same order.
- Apply it wherever a judge's to-do order is presented or chosen: the judge queue
  page, the judge-facing assignments API list, and any "next review" pick. Done
  or submitted reviews may keep their current grouping (to-do first, then done),
  but within each group use the per-judge order.
- Organizer views (assignments, progress, results) keep their current sorting;
  they are lookup tables, not a judging sequence.
- Pairwise mode is unaffected (it already schedules by coverage with a hashed
  tie-break).
- No new queries: compute the key in Python over the already-fetched rows, or in
  SQL if simpler, but keep `assertNumQueries` bounds unchanged.

## Tests (add to the existing judging test module that covers the queue)

- Two judges assigned the same five projects see different orders (construct ids
  so this holds deterministically, or assert over several seeded ids).
- One judge's order is identical across two requests and across page and API.
- All assigned projects are present exactly once; to-do before done.
- Update existing assertions that relied on title order, minimally, and say which.

## Docs

- `JUDGING.md`, Assignment section: two sentences on the per-judge order and why.
- `docs/REAL-WORLD-JUDGING.md`: in the table row for failure 5 and in section 4,
  replace the "queued" wording with what is now implemented (keep "the model does
  not correct order effects").

## Scope

Touch only `src/judging/policy.py` (and the judge queue view/API function that
orders the list, if ordering happens there), the judging test module, `JUDGING.md`
(Assignment section only), `docs/REAL-WORLD-JUDGING.md` (row 5 and section 4
wording only). Do not touch tour, showcase, results, templates or docs/API.md.
Never git commit.

## Done means

The judging test module(s) you touched pass, plus `manage.py test
tests.test_adversarial_t2`. Finish with SUMMARY / FILES / TESTS / GAPS.
