"""The synthetic calibration showcase: a third seeded event with a known truth.

Pure module: standard library only, so the fixtures-shaped dict can be generated
and diffed without a database. ``build()`` is what
``interop.importer.import_fixture`` consumes, so the showcase obeys exactly the
same data-model rules as the organizers' ``fixtures.json``; ``truth()`` returns
the values that were planted, which only this event has.

Design (fixed, declared, not tuned until it looked good):

* 24 projects whose true quality is spread evenly from 1.8 to 4.4 and then
  assigned to project slots in a shuffled order, so the strongest project is not
  the first one listed.
* 12 judges: 2 harsh (offset -1.0), 2 generous (+1.0), 8 neutral with an offset
  drawn from N(0, 0.15).
* Every project gets 3 reviews and every judge 6, which is 72 reviews both ways.
* The routing is deliberate and is the failure mode normalization exists for: the
  harsh judges are routed to the strongest projects and the generous judges to
  the middling ones, so raw means drag the top of the table down and push the
  middle up by construction. A method that only recovers the truth on
  well-distributed assignments has not been shown to work at all.
* Per criterion, a review scores ``clamp(round(quality + offset + N(0, 0.5)), 1, 5)``.

Judging is closed and nothing is published, so the whole publish flow can be
rehearsed without touching a real event.
"""
from __future__ import annotations

import random
from collections import defaultdict

#: The one seed. Every number below is a function of it, never of dict order,
#: the clock or the hash seed.
SHOWCASE_SEED = 20260120
SHOWCASE_SLUG = "showcase"
SHOWCASE_SOURCE_ID = "evt_showcase"
SHOWCASE_NAME = "Calibration showcase (synthetic)"
SHOWCASE_TAGLINE = "Synthetic data with a known true order, for rehearsing the publish flow."
SHOWCASE_DESCRIPTION = (
    "A generated event for checking the scoring method. 24 synthetic projects have a "
    "true quality that is known to the generator but never stored, and 12 synthetic "
    "judges have planted scoring habits: two harsh, two generous, the rest neutral. "
    "The judge-to-project routing is deliberately uneven. Judging is closed and no "
    "results are published, so the whole publish flow can be rehearsed here without "
    "touching a real event."
)
SUBMISSIONS_OPEN = "2026-01-05T09:00:00Z"
SUBMISSIONS_CLOSE = "2026-01-20T18:00:00Z"

CRITERIA = ("functionality", "quality", "innovation")
SCORE_MIN = 1
SCORE_MAX = 5

PROJECT_COUNT = 24
JUDGE_COUNT = 12
#: One team and one member per project: the data model allows only one active
#: project per team (projects.project_one_active_per_team).
TEAM_COUNT = 24
MEMBERS_PER_TEAM = 1
REVIEWS_PER_PROJECT = 3
REVIEWS_PER_JUDGE = 6
#: Strongest, middling and weakest slice of the quality ladder.
TIER_SIZE = 8

QUALITY_LOW = 1.8
QUALITY_HIGH = 4.4
QUALITY_STEP = 2  # decimals kept in the planted values
HARSH_OFFSET = -1.0
GENEROUS_OFFSET = 1.0
NEUTRAL_OFFSET_SD = 0.15
CRITERION_NOISE_SD = 0.5

#: A planted offset this far from zero counts as a planted habit. The neutral
#: draw is 3.3 sigma away at 0.5, so the label cannot flip by accident.
PLANTED_HABIT = 0.5

#: Judge indices, by role. Fixed so a habit is never identifiable from a name.
HARSH_JUDGES = (0, 1)
GENEROUS_JUDGES = (2, 3)
NEUTRAL_JUDGES = (4, 5, 6, 7, 8, 9, 10, 11)

DESIGN = (
    "Every project's three reviews are routed by planted habit: the two harsh judges "
    "cover the eight strongest projects, the two generous judges the eight middling "
    "ones, and the eight neutral judges take the remaining capacity, so raw means are "
    "pushed down at the top of the table and up in the middle by construction."
)

TRACKS = (
    ("trk_sc01", "Builder tools", "Tools, libraries and small pieces of infrastructure."),
    ("trk_sc02", "Everyday systems", "Things a group, a class or a street actually uses."),
)

#: (title, summary) for the 24 projects. Order carries no quality information:
#: the quality ladder is shuffled onto these slots by the seeded generator.
PROJECTS = (
    ("Tide Ledger", "Replaces a paper shift log with a searchable ledger."),
    ("Beacon Cache", "Offline map tiles that keep working without a signal."),
    ("Meadow Board", "A shared planning board for community growing plots."),
    ("Kettle Notes", "Recipe notes that scale, with the timings kept."),
    ("Quiet Hours", "A booking calendar for shared studio spaces."),
    ("Ferry Watch", "Live departure boards for small coastal routes."),
    ("Lantern Index", "Finds a lamp by how bright it is, not by name."),
    ("Copper Grid", "Heat-pump readings for a block of flats."),
    ("Willow Sluice", "Water-level history for a village stream."),
    ("Ember Notes", "A lab notebook for sourdough experiments."),
    ("Harbour Sums", "Splits a shared dinner bill without arguing."),
    ("Marrow Press", "A tiny typesetter for posters, runs in a browser."),
    ("Nettle Chart", "Charts a week of symptoms from a paper diary."),
    ("Pigeon Post", "Printable mailing labels for a community letter drop."),
    ("Quarry Books", "A lending library that fits on one shelf."),
    ("Ridge Notes", "Turns field recordings into a map of noise."),
    ("Salt Marsh", "A water-quality log for a school nature reserve."),
    ("Thistle Board", "A kanban board for a two-person studio."),
    ("Umber Frame", "Aligns scanned drawings to a grid."),
    ("Vellum Draft", "A review queue for long documents."),
    ("Wicker Plan", "Plans a vegetable patch from what you already grow."),
    ("Yarrow Feed", "A reading list that tracks what you actually finished."),
    ("Zephyr Sift", "Filters a noisy sensor feed down to the useful spikes."),
    ("Bramble Log", "A shared log for tool repairs at a repair cafe."),
)

COMMENTS = (
    "Synthetic review: the build runs from a clean checkout.",
    "Synthetic review: read the write-up and tried the demo.",
    "Synthetic review: solid, with rough edges in the setup.",
    "Synthetic review: clear scope, ordinary polish.",
    "Synthetic review: works, and the write-up explains why.",
    "Synthetic review: the tests cover the main path.",
)


def quality_ladder() -> tuple[float, ...]:
    """The 24 true quality values, evenly spread from 1.8 to 4.4."""
    span = QUALITY_HIGH - QUALITY_LOW
    return tuple(
        round(QUALITY_LOW + index * span / (PROJECT_COUNT - 1), QUALITY_STEP)
        for index in range(PROJECT_COUNT)
    )


def _clamp(value: int) -> int:
    return max(SCORE_MIN, min(SCORE_MAX, value))


def _judge_offsets(rng: random.Random) -> tuple[float, ...]:
    """Planted offsets in criterion points, harsh first, neutral drawn last."""
    offsets = [0.0] * JUDGE_COUNT
    for index in HARSH_JUDGES:
        offsets[index] = HARSH_OFFSET
    for index in GENEROUS_JUDGES:
        offsets[index] = GENEROUS_OFFSET
    for index in NEUTRAL_JUDGES:
        offsets[index] = rng.gauss(0.0, NEUTRAL_OFFSET_SD)
    return tuple(offsets)


def _routing_plan(quality: tuple[float, ...]) -> list[tuple[int, int]]:
    """(judge index, project slot) edges: 3 per project, 6 per judge, no repeats.

    The tier split is the deliberate part: harsh judges take the strongest
    projects and generous judges the middling ones, so the raw mean of a strong
    project is pulled down and the raw mean of a middling one is pushed up. The
    remaining capacity is then dealt to the neutral judges in tiers, strongest
    slot first, one continuous cycle, which gives every neutral judge exactly
    ``REVIEWS_PER_JUDGE`` projects spread over all three tiers.
    """
    order = sorted(range(PROJECT_COUNT), key=lambda slot: (-quality[slot], slot))
    strong = order[:TIER_SIZE]
    middle = order[TIER_SIZE : 2 * TIER_SIZE]
    weak = order[2 * TIER_SIZE :]
    capacity = {slot: REVIEWS_PER_PROJECT for slot in range(PROJECT_COUNT)}
    seen: dict[int, set[int]] = defaultdict(set)
    edges: list[tuple[int, int]] = []

    def give(judge: int, slot: int) -> None:
        capacity[slot] -= 1
        seen[judge].add(slot)
        edges.append((judge, slot))

    def take_tier(judge: int, tier: list[int], start: int) -> None:
        for slot in tier[start:] + tier[:start]:
            if capacity[slot] > 0:
                give(judge, slot)
                if len(seen[judge]) == REVIEWS_PER_JUDGE:
                    return

    for index, start in ((HARSH_JUDGES[0], 0), (HARSH_JUDGES[1], TIER_SIZE // 2)):
        take_tier(index, strong, start)
    for index, start in ((GENEROUS_JUDGES[0], 0), (GENEROUS_JUDGES[1], TIER_SIZE // 2)):
        take_tier(index, middle, start)

    cursor = 0
    for position in range(TIER_SIZE):
        for tier in (strong, middle, weak):
            slot = tier[position]
            for _ in range(capacity[slot]):
                give(NEUTRAL_JUDGES[cursor % len(NEUTRAL_JUDGES)], slot)
                cursor += 1
    return edges


def _generate() -> dict:
    """Everything the showcase is, in one deterministic pass over the seed."""
    rng = random.Random(SHOWCASE_SEED)
    ladder = quality_ladder()
    order = list(range(PROJECT_COUNT))
    rng.shuffle(order)
    quality = tuple(ladder[order[slot]] for slot in range(PROJECT_COUNT))
    offsets = _judge_offsets(rng)
    edges = _routing_plan(quality)

    tracks = [
        {"id": track_id, "name": name, "description": description}
        for track_id, name, description in TRACKS
    ]
    judges = [
        {
            "id": f"jdg_sc{index + 1:02d}",
            "name": f"Showcase Judge {index + 1:02d}",
            "email": f"showcase.judge{index + 1:02d}@example.org",
            "tracks": [track_id for track_id, _name, _description in TRACKS],
        }
        for index in range(JUDGE_COUNT)
    ]
    teams = [
        {
            "id": f"tm_sc{index + 1:02d}",
            "name": f"Showcase Team {index + 1:02d}",
            "members": [
                f"showcase.member{index * MEMBERS_PER_TEAM + offset + 1:02d}@example.org"
                for offset in range(MEMBERS_PER_TEAM)
            ],
        }
        for index in range(TEAM_COUNT)
    ]
    projects = [
        {
            "id": f"prj_sc{slot + 1:02d}",
            "team": f"tm_sc{slot + 1:02d}",
            "track": f"trk_sc{slot % len(tracks) + 1:02d}",
            "title": title,
            "summary": summary,
            "description": f"{summary} Synthetic showcase entry.",
            "repo_url": f"https://example.org/showcase/{slot + 1:02d}",
            "submitted_at": f"2026-01-12T{9 + slot // 12:02d}:{(slot * 5) % 60:02d}:00Z",
        }
        for slot, (title, summary) in enumerate(PROJECTS)
    ]

    scores = []
    for judge_index, slot in sorted(edges):
        scores.append({
            "judge": judges[judge_index]["id"],
            "project": projects[slot]["id"],
            "criteria": {
                criterion: _clamp(
                    round(quality[slot] + offsets[judge_index] + rng.gauss(0.0, CRITERION_NOISE_SD))
                )
                for criterion in CRITERIA
            },
            "comment": COMMENTS[rng.randrange(len(COMMENTS))],
        })

    return {
        "event": {
            "id": SHOWCASE_SOURCE_ID,
            "name": SHOWCASE_NAME,
            "tagline": SHOWCASE_TAGLINE,
            "description": SHOWCASE_DESCRIPTION,
            "submissions_open": SUBMISSIONS_OPEN,
            "submissions_close": SUBMISSIONS_CLOSE,
        },
        "tracks": tracks,
        "judges": judges,
        "teams": teams,
        "projects": projects,
        "scores": scores,
        "quality": quality,
        "offsets": offsets,
    }


def build() -> dict:
    """The showcase as a fixtures-shaped dict for ``import_fixture``."""
    data = _generate()
    return {
        "event": data["event"],
        "tracks": data["tracks"],
        "judges": data["judges"],
        "teams": data["teams"],
        "projects": data["projects"],
        "scores": data["scores"],
    }


def truth() -> dict:
    """The planted values only the generator knows: offsets, qualities, routing."""
    data = _generate()
    return {
        "judge_offsets": {
            judge["email"]: offset for judge, offset in zip(data["judges"], data["offsets"])
        },
        "quality": {
            project["title"]: quality
            for project, quality in zip(data["projects"], data["quality"])
        },
        "design": DESIGN,
    }


def judge_directory() -> dict[str, dict[str, str]]:
    """Judge public id -> ``{"email", "name"}``, for pages that compare to the truth."""
    return {
        judge["id"]: {"email": judge["email"], "name": judge["name"]}
        for judge in _generate()["judges"]
    }


def planted_habit(offset: float) -> str:
    """The habit a planted offset claims: harsh, generous or neutral."""
    if offset <= -PLANTED_HABIT:
        return "harsh"
    if offset >= PLANTED_HABIT:
        return "generous"
    return "neutral"


def is_showcase_event(event) -> bool:
    """True for the showcase event, whose planted truth this portal still holds.

    Matched on provenance, not on the slug: only the imported generator output
    carries a truth, and the slug is a display string an organizer can change.
    """
    return getattr(event, "source_id", None) == SHOWCASE_SOURCE_ID
