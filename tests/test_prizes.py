"""Prize allocation: the pure allocator (`results.prizes`) and its use in results.

Covers: overall vs track pools, the one-prize-per-team cascade, exact ties
at a cut, unranked/withdrawn projects never winning, places, determinism,
and the digest/verify coverage of the awards (BUILD-SPEC 16).
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db.utils import IntegrityError
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from events.models import Event, EventRole, Prize, PrizeScope, Role, Track
from judging.models import (
    Assignment,
    Criterion,
    CriterionScore,
    Review,
    ReviewStatus,
    Rubric,
)
from projects.models import Project, ProjectStatus
from results import prizes, services
from teams.models import Team


def _row(project_id, title, team_id, score, track_id=None, status=prizes.RANKED,
         rank=None, team_name=None):
    return prizes.RankedProject(
        project_id=project_id,
        title=title,
        team_id=team_id,
        team_name=team_name or f"Team {team_id}",
        track_id=track_id,
        track_name=f"Track {track_id}" if track_id else None,
        score=score,
        rank=rank,
        status=status,
    )


def _prize(prize_id, name, scope=prizes.SCOPE_OVERALL, track_id=None, places=1,
           track_name=""):
    return prizes.PrizeSpec(
        prize_id=prize_id, name=name, scope=scope, track_id=track_id, places=places,
        track_name=track_name,
    )


# ---------------------------------------------------------------------------
# Pure allocator
# ---------------------------------------------------------------------------

class AllocateTests(TestCase):
    """No database: results.prizes is a pure function of its inputs."""

    def setUp(self):
        self.rows = [
            _row("prj_1", "Glass Signal", "tm_1", 88.0, "trk_a", rank="1"),
            _row("prj_2", "Solar Loop", "tm_2", 74.0, "trk_b", rank="2"),
            _row("prj_3", "River Bank", "tm_3", 61.0, "trk_a", rank="3"),
        ]
        self.grand = _prize("prz_1", "Grand Prize")
        self.runner = _prize("prz_2", "Runner-up")

    def test_overall_prize_takes_the_top_ranked_project(self):
        alloc = prizes.allocate(self.rows, [self.grand])
        self.assertEqual(len(alloc.awards), 1)
        self.assertEqual(alloc.awards[0].project_id, "prj_1")
        self.assertEqual(alloc.awards[0].place, 1)
        self.assertEqual(alloc.unawarded, ())

    def test_overall_prize_draws_across_tracks(self):
        """Overall prizes rank across tracks: second place is in another track."""
        alloc = prizes.allocate(self.rows, [self.grand, self.runner])
        self.assertEqual([a.project_id for a in alloc.awards], ["prj_1", "prj_2"])
        self.assertEqual(alloc.awards[1].track, "Track trk_b")

    def test_track_prize_only_draws_from_its_track(self):
        track_prize = _prize("prz_3", "Climate Prize", prizes.SCOPE_TRACK, "trk_a")
        alloc = prizes.allocate(self.rows, [track_prize])
        self.assertEqual(alloc.awards[0].project_id, "prj_1")
        self.assertEqual(alloc.awards[0].track, "Track trk_a")

    def test_track_prize_ignores_the_global_leader(self):
        """prj_1 leads overall but is not in the track, so the track prize skips it."""
        track_prize = _prize("prz_3", "Climate Prize", prizes.SCOPE_TRACK, "trk_b")
        alloc = prizes.allocate(self.rows, [track_prize])
        self.assertEqual(alloc.awards[0].project_id, "prj_2")

    def test_unranked_and_ineligible_rows_never_win(self):
        rows = [
            _row("prj_1", "Draft Entry", "tm_1", 99.0, "trk_a", status="unranked_no_reviews"),
            _row("prj_2", "Withdrawn", "tm_2", 95.0, "trk_a", status="withdrawn"),
            _row("prj_3", "Kicked", "tm_3", 90.0, "trk_a", status="disqualified"),
            _row("prj_4", "Old Version", "tm_4", 85.0, "trk_a", status="superseded"),
            _row("prj_5", "Real Entry", "tm_5", 40.0, "trk_a"),
        ]
        alloc = prizes.allocate(rows, [self.grand])
        self.assertEqual([a.project_id for a in alloc.awards], ["prj_5"])

    def test_one_per_team_cascade_skips_to_the_next_eligible_project(self):
        rows = [
            _row("prj_1", "Glass Signal", "tm_1", 88.0, "trk_a", rank="1"),
            _row("prj_2", "Glass Signal II", "tm_1", 80.0, "trk_a", rank="2"),
            _row("prj_3", "Solar Loop", "tm_2", 74.0, "trk_b", rank="3"),
        ]
        alloc = prizes.allocate(rows, [self.grand, self.runner], one_per_team=True)
        first, second = alloc.awards
        self.assertEqual((first.project_id, first.prize), ("prj_1", "Grand Prize"))
        self.assertEqual(second.project_id, "prj_3")
        self.assertEqual(
            second.reason,
            "Glass Signal II already won Grand Prize; next eligible is Solar Loop",
        )

    def test_no_project_ever_wins_two_prizes(self):
        """A project already holding a prize is out of the running, one-per-team or not."""
        rows = [
            _row("prj_1", "Glass Signal", "tm_1", 88.0, "trk_a", rank="1"),
            _row("prj_2", "Solar Loop", "tm_2", 74.0, "trk_b", rank="2"),
        ]
        alloc = prizes.allocate(rows, [self.grand, self.runner], one_per_team=False)
        self.assertEqual([a.project_id for a in alloc.awards], ["prj_1", "prj_2"])
        self.assertEqual([a.prize for a in alloc.awards], ["Grand Prize", "Runner-up"])

    def test_one_per_team_cascade_honours_track_prize_pool(self):
        """A track prize may not reach outside its track to find a winner."""
        rows = [
            _row("prj_1", "Glass Signal", "tm_1", 88.0, "trk_a", rank="1"),
            _row("prj_2", "Glass Signal II", "tm_1", 80.0, "trk_a", rank="2"),
        ]
        track_prize = _prize("prz_3", "Climate Prize", prizes.SCOPE_TRACK, "trk_a",
                             track_name="Climate")
        alloc = prizes.allocate(rows, [self.grand, track_prize], one_per_team=True)
        self.assertEqual(len(alloc.awards), 1)
        self.assertEqual(alloc.awards[0].project_id, "prj_1")
        self.assertEqual(
            alloc.unawarded[0].reason,
            "Glass Signal II already won Grand Prize; no eligible project remains in Climate",
        )
        self.assertEqual(alloc.unawarded[0].prize_id, "prz_3")

    def test_one_per_team_disabled_lets_one_team_take_every_place(self):
        rows = [
            _row("prj_1", "Glass Signal", "tm_1", 88.0, "trk_a", rank="1"),
            _row("prj_2", "Glass Signal II", "tm_1", 80.0, "trk_a", rank="2"),
        ]
        podium = _prize("prz_9", "Podium", places=2)
        alloc = prizes.allocate(rows, [podium], one_per_team=False)
        self.assertEqual([a.project_id for a in alloc.awards], ["prj_1", "prj_2"])
        self.assertEqual([a.reason for a in alloc.awards], ["", ""])
        self.assertEqual({a["team_id"] for a in alloc.as_dict()["awards"]}, {"tm_1"})

    def test_one_per_team_enabled_leaves_the_second_place_unawarded(self):
        rows = [
            _row("prj_1", "Glass Signal", "tm_1", 88.0, "trk_a", rank="1"),
            _row("prj_2", "Glass Signal II", "tm_1", 80.0, "trk_a", rank="2"),
        ]
        podium = _prize("prz_9", "Podium", places=2)
        alloc = prizes.allocate(rows, [podium], one_per_team=True)
        self.assertEqual([a.project_id for a in alloc.awards], ["prj_1"])
        self.assertEqual(
            alloc.unawarded[0].reason,
            "Glass Signal II already won Podium; no eligible project remains",
        )

    def test_exact_tie_at_the_cut_needs_an_organizer(self):
        rows = [
            _row("prj_1", "Solar Loop", "tm_1", 88.0, "trk_a", rank="1"),
            _row("prj_2", "River Bank", "tm_2", 74.0, "trk_a", rank="=2"),
            _row("prj_3", "Mud Stone", "tm_3", 74.0, "trk_a", rank="=2"),
        ]
        alloc = prizes.allocate(rows, [self.grand, self.runner])
        self.assertEqual([a.project_id for a in alloc.awards], ["prj_1"])
        tie = alloc.unawarded[0]
        self.assertEqual(tie.prize_id, "prz_2")
        self.assertEqual(tie.projects, ("prj_2", "prj_3"))
        self.assertIn("tie requires organizer decision", tie.reason)
        self.assertIn("River Bank and Mud Stone", tie.reason)
        self.assertIn("74.00", tie.reason)

    def test_tie_with_a_blocked_project_is_decided_by_policy_not_by_tiebreak(self):
        """prj_2 and prj_3 are tied, but prj_2's team already won: the policy decides."""
        rows = [
            _row("prj_1", "Solar Loop", "tm_1", 88.0, "trk_a", rank="1"),
            _row("prj_2", "River Bank", "tm_1", 74.0, "trk_a", rank="=2"),
            _row("prj_3", "Mud Stone", "tm_3", 74.0, "trk_a", rank="=2"),
        ]
        alloc = prizes.allocate(rows, [self.grand, self.runner], one_per_team=True)
        self.assertEqual([a.project_id for a in alloc.awards], ["prj_1", "prj_3"])
        self.assertEqual(
            alloc.awards[1].reason,
            "River Bank already won Grand Prize; next eligible is Mud Stone",
        )
        self.assertEqual(alloc.unawarded, ())

    def test_tie_is_a_tie_at_every_place_not_just_the_first(self):
        rows = [
            _row("prj_1", "A", "tm_1", 90.0, "trk_a", rank="1"),
            _row("prj_2", "B", "tm_2", 80.0, "trk_a", rank="2"),
            _row("prj_3", "C", "tm_3", 80.0, "trk_a", rank="=2"),
            _row("prj_4", "D", "tm_4", 70.0, "trk_a", rank="4"),
        ]
        places2 = _prize("prz_9", "Top Two", places=2)
        alloc = prizes.allocate(rows, [places2])
        self.assertEqual([a.project_id for a in alloc.awards], ["prj_1"])
        self.assertEqual(alloc.unawarded[0].place, 2)

    def test_places_award_that_many_ranked_projects(self):
        places3 = _prize("prz_9", "Podium", places=3)
        alloc = prizes.allocate(self.rows, [places3])
        self.assertEqual([(a.place, a.project_id) for a in alloc.awards],
                         [(1, "prj_1"), (2, "prj_2"), (3, "prj_3")])

    def test_more_places_than_projects_is_unawarded_not_invented(self):
        places5 = _prize("prz_9", "Podium", places=5)
        alloc = prizes.allocate(self.rows, [places5])
        self.assertEqual(len(alloc.awards), 3)
        self.assertEqual(alloc.unawarded[0].reason, "no ranked project remains")
        self.assertEqual(alloc.unawarded[0].place, 4)

    def test_prizes_are_considered_in_the_given_order(self):
        """Position order decides which prize a team is skipped for."""
        rows = [
            _row("prj_1", "Glass Signal", "tm_1", 88.0, "trk_a"),
            _row("prj_2", "Glass Signal II", "tm_1", 80.0, "trk_a"),
            _row("prj_3", "Solar Loop", "tm_2", 74.0, "trk_b"),
        ]
        grand_first = prizes.allocate(rows, [self.grand, self.runner], one_per_team=True)
        runner_first = prizes.allocate(rows, [self.runner, self.grand], one_per_team=True)
        self.assertEqual([a.project_id for a in grand_first.awards], ["prj_1", "prj_3"])
        self.assertEqual([a.project_id for a in runner_first.awards], ["prj_1", "prj_3"])
        self.assertIn("already won Grand Prize", grand_first.awards[1].reason)
        self.assertIn("already won Runner-up", runner_first.awards[1].reason)

    def test_allocation_is_deterministic(self):
        first = prizes.allocate(self.rows, [self.grand, self.runner]).as_dict()
        for _ in range(5):
            self.assertEqual(prizes.allocate(self.rows, [self.grand, self.runner]).as_dict(),
                             first)

    def test_as_dict_is_json_ready(self):
        payload = prizes.allocate(self.rows, [self.grand]).as_dict()
        self.assertEqual(sorted(payload), ["awards", "unawarded"])
        self.assertEqual(payload["awards"][0]["prize_id"], "prz_1")
        self.assertIsInstance(payload["awards"][0]["place"], int)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class PrizeModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("org@prizes.test", "password")
        past = timezone.now() - timedelta(days=10)
        self.event = Event.objects.create(
            slug="prizes-model", name="Prizes Model",
            submissions_close_at=past, judging_open_at=past,
            judging_close_at=past + timedelta(minutes=1), created_by=self.user,
        )
        self.track = Track.objects.create(event=self.event, name="Climate")

    def test_prize_defaults(self):
        prize = Prize.objects.create(event=self.event, name="Grand Prize")
        self.assertEqual(prize.scope, PrizeScope.OVERALL)
        self.assertEqual(prize.places, 1)
        self.assertEqual(prize.eligibility_note, "")

    def test_event_one_prize_per_team_defaults_to_true(self):
        self.assertTrue(Event.objects.get(pk=self.event.pk).one_prize_per_team)

    def test_a_prize_with_a_track_is_a_track_prize(self):
        prize = Prize.objects.create(event=self.event, name="Climate Prize",
                                     track=self.track)
        prize.refresh_from_db()
        self.assertEqual(prize.scope, PrizeScope.TRACK)

    def test_changing_the_track_updates_the_scope(self):
        prize = Prize.objects.create(event=self.event, name="Climate Prize",
                                     track=self.track)
        prize.track = None
        prize.save(update_fields=["track"])
        prize.refresh_from_db()
        self.assertEqual(prize.scope, PrizeScope.OVERALL)

    def test_places_must_be_positive(self):
        with self.assertRaises(IntegrityError):
            Prize.objects.create(event=self.event, name="Broken", places=0)


# ---------------------------------------------------------------------------
# Integration with results services
# ---------------------------------------------------------------------------

class PrizeResultsTests(TestCase):
    """One event, one judge, one criterion: score value v maps to 100*(v-1)/4."""

    @classmethod
    def setUpTestData(cls):
        past = timezone.now() - timedelta(days=10)
        cls.organizer = User.objects.create_user("org@prizes.test", "password")
        cls.judge_user = User.objects.create_user("judge@prizes.test", "password")
        cls.event = Event.objects.create(
            slug="prizes-test", name="Prizes Test",
            submissions_close_at=past, judging_open_at=past,
            judging_close_at=past + timedelta(minutes=1),
            shrinkage_lambda=Decimal("2.00"), created_by=cls.organizer,
        )
        EventRole.objects.create(event=cls.event, user=cls.organizer,
                                 role=Role.ORGANIZER, public_id="org_prz")
        cls.judge = EventRole.objects.create(event=cls.event, user=cls.judge_user,
                                            role=Role.JUDGE, public_id="jdg_prz")
        cls.track_a = Track.objects.create(event=cls.event, name="Open Track", position=1)
        cls.track_b = Track.objects.create(event=cls.event, name="Climate", position=2)
        rubric = Rubric.objects.create(event=cls.event, version=1)
        cls.criterion = Criterion.objects.create(
            rubric=rubric, key="quality", name="Quality",
            weight=Decimal("1.000"), min_score=1, max_score=5, position=0,
        )
        cls._n = 0

    def _project(self, title, track, value=None, team=None, status=ProjectStatus.SUBMITTED):
        cls_n = PrizeResultsTests._n = PrizeResultsTests._n + 1
        team = team or Team.objects.create(event=self.event, name=f"Team {cls_n}")
        project = Project.objects.create(
            event=self.event, team=team, track=track,
            title=title, status=status, revision=1,
        )
        if value is not None:
            self._score(project, value)
        return project

    def _score(self, project, value):
        assignment = Assignment.objects.create(
            event=self.event, judge=self.judge, project=project,
        )
        review = Review.objects.create(
            event=self.event, assignment=assignment, judge=self.judge, project=project,
            status=ReviewStatus.SUBMITTED, submitted_at=timezone.now() - timedelta(days=5),
            rubric_version=1, project_revision=1,
        )
        return CriterionScore.objects.create(review=review, criterion=self.criterion,
                                             value=value)

    def _prize(self, name, position, **kwargs):
        return Prize.objects.create(event=self.event, name=name, position=position, **kwargs)

    def test_preview_shows_the_proposed_awards(self):
        top = self._project("Glass Signal", self.track_a, value=5)
        self._project("Solar Loop", self.track_b, value=3)
        self._prize("Grand Prize", 1)
        data = services.preview(self.event)
        self.assertEqual(len(data["awards"]), 1)
        self.assertEqual(data["awards"][0]["project_id"], top.public_id)
        self.assertEqual(data["awards"][0]["prize"], "Grand Prize")
        self.assertEqual(data["awards"][0]["place"], 1)
        self.assertEqual(data["unawarded"], [])

    def test_track_prize_awards_inside_its_track_only(self):
        self._project("Glass Signal", self.track_a, value=5)
        climate = self._project("River Bank", self.track_b, value=3)
        self._project("Mud Stone", self.track_b, value=2)
        self._prize("Climate Prize", 1, track=self.track_b)
        data = services.preview(self.event)
        self.assertEqual(len(data["awards"]), 1)
        self.assertEqual(data["awards"][0]["project_id"], climate.public_id)
        self.assertEqual(data["awards"][0]["track"], "Climate")

    def test_track_prize_skips_the_overall_winner(self):
        """The overall winner cannot also take their own track prize."""
        winner = self._project("Glass Signal", self.track_b, value=5)
        second = self._project("River Bank", self.track_b, value=4)
        self._project("Mud Stone", self.track_a, value=3)
        self._prize("Grand Prize", 1)
        self._prize("Climate Prize", 2, track=self.track_b)
        data = services.preview(self.event)
        awards = {a["prize"]: a for a in data["awards"]}
        self.assertEqual(awards["Grand Prize"]["project_id"], winner.public_id)
        self.assertEqual(awards["Climate Prize"]["project_id"], second.public_id)
        self.assertNotIn(winner.public_id, [a["project_id"] for a in data["awards"]][1:])
        self.assertEqual(data["unawarded"], [])

    def test_one_per_team_flag_cannot_double_award_a_project(self):
        """A team has one active project, so the flag can never double-award one."""
        self._project("Glass Signal", self.track_b, value=5)
        self._project("River Bank", self.track_b, value=4)
        self._prize("Grand Prize", 1)
        self._prize("Climate Prize", 2, track=self.track_b)
        with_flag = services.preview(self.event)["awards"]
        Event.objects.filter(pk=self.event.pk).update(one_prize_per_team=False)
        self.event.refresh_from_db()
        without_flag = services.preview(self.event)["awards"]
        self.assertEqual(with_flag, without_flag)
        winners = [a["project_id"] for a in without_flag]
        self.assertEqual(len(winners), len(set(winners)))

    def test_tie_at_the_cut_is_reported_for_an_organizer(self):
        self._project("Solar Loop", self.track_a, value=5)
        tied_a = self._project("River Bank", self.track_b, value=4)
        tied_b = self._project("Mud Stone", self.track_b, value=4)
        self._prize("Grand Prize", 1)
        self._prize("Runner-up", 2)
        data = services.preview(self.event)
        self.assertEqual(len(data["awards"]), 1)
        self.assertEqual(data["awards"][0]["prize"], "Grand Prize")
        self.assertEqual(len(data["unawarded"]), 1)
        tie = data["unawarded"][0]
        self.assertIn("tie requires organizer decision", tie["reason"])
        self.assertEqual(set(tie["projects"]), {tied_a.public_id, tied_b.public_id})

    def test_unranked_project_never_wins(self):
        self._project("Glass Signal", self.track_a, value=5)
        unranked = self._project("Never Reviewed", self.track_a)
        self._prize("Grand Prize", 1)
        self._prize("Runner-up", 2)
        data = services.preview(self.event)
        awarded = {a["project_id"] for a in data["awards"]}
        self.assertEqual(awarded, {Project.objects.get(title="Glass Signal").public_id})
        self.assertNotIn(unranked.public_id, awarded)

    def test_withdrawn_project_never_wins(self):
        withdrawn = self._project("Pulled", self.track_a, value=5,
                                  status=ProjectStatus.WITHDRAWN)
        keeper = self._project("Glass Signal", self.track_a, value=4)
        self._prize("Grand Prize", 1)
        data = services.preview(self.event)
        self.assertEqual([a["project_id"] for a in data["awards"]], [keeper.public_id])
        self.assertNotIn(withdrawn.public_id, [a["project_id"] for a in data["awards"]])

    def test_preview_awards_are_deterministic(self):
        self._project("Glass Signal", self.track_a, value=5)
        self._project("Solar Loop", self.track_b, value=3)
        self._prize("Grand Prize", 1)
        first = services.preview(self.event)["awards"]
        self.assertEqual(first, services.preview(self.event)["awards"])


class PublicationPrizeTests(PrizeResultsTests):
    """Publishing, the public payload, the digest and verify."""

    def _published(self, **kwargs):
        pub = services.publish(self.organizer, self.event, note="prizes", **kwargs)
        self.addCleanup(pub.delete)
        return pub

    def test_publish_stores_the_awards_on_the_snapshot(self):
        winner = self._project("Glass Signal", self.track_a, value=5)
        self._prize("Grand Prize", 1)
        pub = self._published()
        self.assertEqual(len(pub.awards), 1)
        self.assertEqual(pub.awards[0]["project_id"], winner.public_id)
        self.assertEqual(pub.unawarded, [])

    def test_public_results_payload_lists_the_awards(self):
        winner = self._project("Glass Signal", self.track_a, value=5)
        self._prize("Grand Prize", 1, value="$1,500")
        self._published()
        data = services.public_results(self.event)
        self.assertEqual(len(data["awards"]), 1)
        self.assertEqual(data["awards"][0]["project_id"], winner.public_id)
        self.assertEqual(data["awards"][0]["team"], winner.team.name)
        self.assertEqual(data["unawarded"], [])

    def test_public_payload_carries_the_eligibility_note(self):
        winner = self._project("Glass Signal", self.track_a, value=5)
        self._prize("Grand Prize", 1, eligibility_note="Must ship a running demo")
        self._published()
        data = services.public_results(self.event)
        self.assertEqual(data["awards"][0]["project_id"], winner.public_id)
        self.assertEqual(data["awards"][0]["note"], "Must ship a running demo")

    def test_public_payload_carries_an_organizer_tie(self):
        self._project("Solar Loop", self.track_a, value=5)
        self._project("River Bank", self.track_b, value=4)
        self._project("Mud Stone", self.track_b, value=4)
        self._prize("Grand Prize", 1)
        self._prize("Runner-up", 2)
        self._published()
        data = services.public_results(self.event)
        self.assertEqual(len(data["unawarded"]), 1)
        self.assertIn("tie requires organizer decision", data["unawarded"][0]["reason"])

    def test_prize_config_is_part_of_the_stored_inputs(self):
        self._project("Glass Signal", self.track_a, value=5)
        prize = self._prize("Grand Prize", 1)
        self._prize("Climate Prize", 2, track=self.track_b)
        pub = self._published()
        stored = {p["prize_id"]: p for p in pub.inputs["prizes"]}
        self.assertEqual(stored[prize.public_id]["scope"], "overall")
        self.assertEqual(stored[prize.public_id]["places"], 1)
        self.assertTrue(pub.inputs["one_prize_per_team"])

    def test_digest_changes_when_a_prize_is_added(self):
        self._project("Glass Signal", self.track_a, value=5)
        before = services.preview(self.event)["input_digest"]
        self._prize("Grand Prize", 1)
        after = services.preview(self.event)["input_digest"]
        self.assertNotEqual(before, after)

    def test_digest_changes_when_the_one_per_team_policy_changes(self):
        self._project("Glass Signal", self.track_a, value=5)
        before = services.preview(self.event)["input_digest"]
        Event.objects.filter(pk=self.event.pk).update(one_prize_per_team=False)
        self.event.refresh_from_db()
        after = services.preview(self.event)["input_digest"]
        self.assertNotEqual(before, after)

    def test_verify_recomputes_the_awards(self):
        self._project("Glass Signal", self.track_a, value=5)
        self._project("Solar Loop", self.track_b, value=3)
        self._prize("Grand Prize", 1, eligibility_note="Must ship a running demo")
        self._prize("Runner-up", 2)
        pub = self._published()
        result = services.verify_publication(pub)
        self.assertTrue(result["awards_match"])
        self.assertEqual(result["verdict"], "identical")

    def test_verify_notices_a_tampered_award(self):
        self._project("Glass Signal", self.track_a, value=5)
        self._project("Solar Loop", self.track_b, value=3)
        self._prize("Grand Prize", 1)
        self._prize("Runner-up", 2)
        pub = self._published()
        pub.awards = []
        pub.save(update_fields=["awards"])
        result = services.verify_publication(pub)
        self.assertFalse(result["awards_match"])
        self.assertEqual(result["verdict"], "differs")

    def test_verify_notices_a_prize_removed_after_publication(self):
        self._project("Glass Signal", self.track_a, value=5)
        self._project("Solar Loop", self.track_b, value=3)
        self._prize("Grand Prize", 1)
        self._prize("Runner-up", 2)
        pub = self._published()
        Prize.objects.filter(event=self.event, name="Runner-up").delete()
        result = services.verify_publication(pub)
        self.assertFalse(result["digest_match"])
        self.assertEqual(result["verdict"], "differs")

    def test_decision_record_shows_the_prizes_and_awards(self):
        self._project("Glass Signal", self.track_a, value=5)
        self._prize("Grand Prize", 1)
        pub = self._published()
        record = services.decision_record(pub)
        self.assertEqual(len(record["awards"]), 1)
        self.assertEqual(len(record["prizes"]), 1)
        self.assertIn("organizer", record["rule_plain"])
        self.assertIn("Prizes", record["rule_plain"])
