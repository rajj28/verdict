"""Adversarial T2: private judge notes and malformed stored publications.

Review.comment is collected on judge/review.html as "private to you and the
organizers, never shown to the team", so released team feedback must never
carry it. The authoring judge and organizers of the same event keep their
existing authorized routes to the note.

Stored publications are persisted JSON: verifying one whose canonical inputs
are malformed must fail explicitly, never crash or report "identical".
Verification also re-hashes the stored inputs against the stored digest and
checks the stored rows against the live non-draft project roster.
"""
from __future__ import annotations

import copy
import json

from django.db.models import JSONField, Value

from accounts.models import User
from events.models import Event, EventRole, Prize, Role
from judging.models import Review
from projects.models import Project, ProjectStatus
from results import services
from results.models import ResultPublication
from teams.models import Team, TeamMember
from tests.test_results import ResultsTestCase

MARKER = "T2-PRIVATE-NOTE-9c41e7"

MALFORMED_INPUTS = [
    ("params null", ("params",), None),
    ("params list", ("params",), ["normalized"]),
    ("lam text", ("params", "lam"), "not-a-number"),
    ("lam list", ("params", "lam"), [2.0]),
    ("lam negative", ("params", "lam"), -1),
    ("method null", ("params", "method"), None),
    ("criteria null item", ("params", "criteria"), [None]),
    ("criteria weight text", ("params", "criteria"),
     [{"key": "quality", "weight": "heavy", "min_score": 1, "max_score": 5}]),
    ("included null", ("included",), None),
    ("included item list", ("included", 0), ["rev_x"]),
    ("review criteria null", ("included", 0, "criteria"), None),
    ("review value text", ("included", 0, "criteria", "quality"), "five"),
    ("review value out of range", ("included", 0, "criteria", "quality"), 99),
    ("comparisons null item", ("comparisons",), [None]),
    ("projects null item", ("projects",), [None]),
    ("prizes null item", ("prizes",), [None]),
    ("prize places text", ("prizes",), [{"prize_id": "prz_x", "name": "X", "scope": "overall", "places": "two"}]),
]


def _criteria(**overrides) -> list[dict]:
    """The fixture rubric as stored criteria, with per-key field overrides."""
    return [{"key": key, "weight": 1.0, "min_score": 1, "max_score": 5, **overrides.get(key, {})}
            for key in ("quality", "innovation")]


def _cancelling_weights(inputs: dict) -> dict:
    """Weights 1, -1 and 5e-324 sum to 5e-324, so the first review scores inf."""
    inputs = copy.deepcopy(inputs)
    inputs["params"]["criteria"] = _criteria(innovation={"weight": -1.0}) + [
        {"key": "extra", "weight": 5e-324, "min_score": 1, "max_score": 5}]
    for review in inputs["included"]:
        review["criteria"]["extra"] = 1
    inputs["included"][0]["criteria"].update(quality=5, innovation=1)
    return inputs


# Rehashed: input_digest is recomputed over the tampered inputs, so each case
# must be caught by the input checks, not by the stored-digest comparison.
REHASHED_MALFORMED = [
    ("review missing a criterion score", lambda i: _replaced(i, ("included", 0, "criteria"), {"quality": 4})),
    ("prize without name", lambda i: _replaced(
        i, ("prizes",), [{"prize_id": "prz_x", "scope": "overall", "places": 1}])),
    ("prize position text", lambda i: _replaced(
        i, ("prizes",), [{"prize_id": "prz_x", "name": "X", "scope": "overall", "places": 1, "position": "1st"}])),
    ("lam giant integer", lambda i: _replaced(i, ("params", "lam"), 10**400)),
    ("criterion weight giant integer", lambda i: _replaced(
        i, ("params", "criteria"), _criteria(quality={"weight": 10**400}))),
    ("criterion max score giant integer", lambda i: _replaced(
        i, ("params", "criteria"), _criteria(quality={"max_score": 10**400}))),
    ("weights cancelling to a denormal", _cancelling_weights),
]


def _body(resp) -> str:
    raw = b"".join(resp.streaming_content) if resp.streaming else resp.content
    return raw.decode()


def _replaced(payload, path: tuple, value):
    """A deep copy of payload with the item at path replaced by value."""
    payload = copy.deepcopy(payload)
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return payload


class PrivateJudgeNotesTests(ResultsTestCase):
    """Published, feedback-released event whose review of project1 holds a unique private marker."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Review.objects.filter(pk=cls.review1_1.pk).update(comment=f"{MARKER} demo crashed twice")
        cls.peer = User.objects.create_user("peer@t2.test", "password")
        EventRole.objects.create(event=cls.event, user=cls.peer, role=Role.PARTICIPANT, public_id="par_t2peer")
        TeamMember.objects.create(team=cls.team2, user=cls.peer, event=cls.event, is_owner=True)
        cls.outside_organizer = User.objects.create_user("outside@t2.test", "password")
        other_event = Event.objects.create(
            slug="t2-other", name="Other Event", submissions_close_at=cls.event.submissions_close_at,
            created_by=cls.outside_organizer,
        )
        EventRole.objects.create(
            event=other_event, user=cls.outside_organizer, role=Role.ORGANIZER, public_id="org_t2out",
        )
        cls.pub = services.publish(cls.organizer, cls.event, note="T2 privacy")
        services.release_feedback(cls.organizer, cls.event)
        cls.feedback_url = f"/api/v1/events/{cls.event.slug}/projects/{cls.project1.public_id}/feedback"
        cls.reviews_csv_url = f"/api/v1/events/{cls.event.slug}/exports/reviews.csv"

    def test_owning_team_feedback_omits_private_note(self):
        self.client.force_login(self.participant_user)
        resp = self.client.get(self.feedback_url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(MARKER, _body(resp))
        self.assertEqual(resp.json()["comments"], [])

    def test_owning_team_feedback_keeps_score_summary(self):
        self.client.force_login(self.participant_user)
        data = self.client.get(self.feedback_url).json()
        row = next(r for r in self.pub.rows if r["project_id"] == self.project1.public_id)
        self.assertTrue(data["feedback_released"])
        self.assertEqual(data["pub_id"], self.pub.public_id)
        self.assertEqual(data["rank"], row["rank"])
        self.assertEqual(data["official_score"], row["normalized"])
        self.assertEqual(data["n_reviews"], 2)
        self.assertEqual(data["per_criterion"], {"quality": 4.5, "innovation": 4.5})

    def test_feedback_service_never_returns_review_comments(self):
        for project in (self.project1, self.project2):
            with self.subTest(project=project.public_id):
                data = services.project_feedback(self.event, project)
                self.assertEqual(data["comments"], [])
                self.assertNotIn(MARKER, json.dumps(data))

    def test_peer_participant_cannot_read_other_team_feedback(self):
        self.client.force_login(self.peer)
        resp = self.client.get(self.feedback_url)
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn(MARKER, _body(resp))

    def test_unrelated_organizer_cannot_read_feedback_or_review_export(self):
        self.client.force_login(self.outside_organizer)
        for url in (self.feedback_url, self.reviews_csv_url):
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 403)
                self.assertNotIn(MARKER, _body(resp))

    def test_authoring_judge_and_event_organizer_still_read_private_note(self):
        self.client.force_login(self.judge1)
        resp = self.client.get(f"/api/v1/events/{self.event.slug}/judge/reviews/{self.project1.public_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(MARKER, resp.json()["review"]["comment"])
        self.client.force_login(self.organizer)
        resp = self.client.get(self.reviews_csv_url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(MARKER, _body(resp))

    def test_public_results_never_embed_private_note(self):
        for url in (f"/events/{self.event.slug}/results", f"/api/v1/events/{self.event.slug}/results"):
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200)
                self.assertNotIn(MARKER, _body(resp))


class MalformedStoredPublicationTests(ResultsTestCase):
    """Verification recomputes from stored inputs, which it must treat as untrusted JSON."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.pub = services.publish(cls.organizer, cls.event, note="T2 verify")
        cls.verify_url = f"/api/v1/events/{cls.event.slug}/results/publications/{cls.pub.public_id}/verify"

    def test_valid_stored_publication_recomputes_and_verifies(self):
        self.client.force_login(self.organizer)
        resp = self.client.post(self.verify_url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["verdict"], "identical")
        self.assertTrue(data["rows_match"])
        self.assertTrue(data["digest_match"])

    def test_malformed_stored_inputs_fail_verification_explicitly(self):
        self.client.force_login(self.organizer)
        for label, path, value in MALFORMED_INPUTS:
            with self.subTest(label):
                ResultPublication.objects.filter(pk=self.pub.pk).update(
                    inputs=_replaced(self.pub.inputs, path, value),
                )
                resp = self.client.post(self.verify_url)
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data["verdict"], "differs")
                self.assertFalse(data["rows_match"])
                self.assertTrue(data["detail"].startswith("Stored publication cannot be recomputed"))

    def test_malformed_stored_row_fails_verification_explicitly(self):
        ResultPublication.objects.filter(pk=self.pub.pk).update(rows=_replaced(self.pub.rows, (0,), None))
        self.client.force_login(self.organizer)
        data = self.client.post(self.verify_url).json()
        self.assertEqual(data["verdict"], "differs")
        self.assertTrue(data["detail"].startswith("Stored publication cannot be recomputed"))

    def _verify(self, **fields) -> dict:
        ResultPublication.objects.filter(pk=self.pub.pk).update(**fields)
        return services.verify_publication(ResultPublication.objects.get(pk=self.pub.pk))

    def test_consistent_judge_rename_in_stored_inputs_fails_stored_digest(self):
        # jdg_rt1 -> jdg_rt1-renamed keeps the sorted judge order, so the engine output is unchanged.
        renamed = json.loads(json.dumps(self.pub.inputs).replace('"jdg_rt1"', '"jdg_rt1-renamed"'))
        self.assertNotEqual(renamed, self.pub.inputs)
        result = self._verify(inputs=renamed)
        self.assertTrue(result["rows_match"])
        self.assertTrue(result["digest_match"])
        self.assertIs(result.get("stored_inputs_match"), False)
        self.assertEqual(result["verdict"], "differs")
        self.assertIn("stored inputs do not hash to the stored digest", result["detail"].lower())
        # The live database was not touched, so the verdict must not blame it.
        self.assertIn("the stored copy was altered", result["detail"].lower())
        self.assertNotIn("live inputs changed", result["detail"].lower())

    def test_rehashed_malformed_inputs_fail_explicitly(self):
        for label, tamper in REHASHED_MALFORMED:
            with self.subTest(label):
                inputs = tamper(self.pub.inputs)
                result = self._verify(inputs=inputs, input_digest=services._digest(inputs))
                self.assertEqual(result["verdict"], "differs")
                self.assertFalse(result["rows_match"])
                self.assertTrue(result["detail"].startswith("Stored publication cannot be recomputed"),
                                result["detail"])

    def test_rehashed_null_prize_position_verifies_like_omitted(self):
        # Prize.position is never null, so a null is legacy or tampered JSON. Verification must not
        # sort it against position 0 (TypeError); the allocator follows stored order as when omitted.
        Prize.objects.create(event=self.event, name="Grand Prize")
        Prize.objects.create(event=self.event, name="Runner-up", position=1)
        pub = services.publish(self.organizer, self.event, note="T2 prize positions")
        self.assertEqual(len(pub.awards), 2)
        inputs = _replaced(_replaced(pub.inputs, ("prizes", 0, "position"), None), ("prizes", 1, "position"), 0)
        ResultPublication.objects.filter(pk=pub.pk).update(inputs=inputs, input_digest=services._digest(inputs))
        result = services.verify_publication(ResultPublication.objects.get(pk=pub.pk))
        self.assertTrue(result["stored_inputs_match"])
        self.assertTrue(result["rows_match"])
        self.assertIs(result["awards_match"], True)
        self.assertFalse(result["digest_match"])  # the live prizes still hold integer positions
        self.assertEqual(result["verdict"], "differs")

    def test_top_level_null_or_wrong_type_payloads_fail_explicitly(self):
        for field, value in (("inputs", Value(None, JSONField())), ("inputs", []),
                             ("rows", Value(None, JSONField())), ("rows", {})):
            with self.subTest(field=field, value=value):
                result = self._verify(**{"inputs": self.pub.inputs, "rows": self.pub.rows, field: value})
                self.assertEqual(result["verdict"], "differs")
                self.assertTrue(result["detail"].startswith("Stored publication cannot be recomputed"))


class PublicationRowSetTests(ResultsTestCase):
    """Stored rows must cover the live non-draft roster exactly once, whatever the stored list holds."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for public_id, status in (("prj_t2idle", ProjectStatus.SUBMITTED), ("prj_t2gone", ProjectStatus.WITHDRAWN),
                                  ("prj_t2wip", ProjectStatus.DRAFT)):
            team = Team.objects.create(event=cls.event, name=f"Team {public_id}")
            Project.objects.create(event=cls.event, team=team, track=cls.track, public_id=public_id,
                                   title=public_id, status=status, revision=1)
        cls.pub = services.publish(cls.organizer, cls.event, note="T2 rows", acknowledge_unranked=True)

    def _verify_rows(self, rows) -> dict:
        ResultPublication.objects.filter(pk=self.pub.pk).update(rows=rows)
        return services.verify_publication(ResultPublication.objects.get(pk=self.pub.pk))

    def test_unchanged_rows_with_unreviewed_and_withdrawn_projects_verify(self):
        self.assertEqual({row["project_id"]: row["status"] for row in self.pub.rows}, {
            "prj_rt1": "ranked", "prj_rt2": "ranked",
            "prj_t2idle": "unranked_no_reviews", "prj_t2gone": "withdrawn",
        })
        result = self._verify_rows(self.pub.rows)
        self.assertEqual(result["verdict"], "identical")
        self.assertTrue(result["rows_match"])

    def test_removed_duplicated_or_fabricated_rows_differ(self):
        rows = self.pub.rows
        winner = next(row for row in rows if row["rank"] == "1")
        unranked = {"title": "Fake", "rank": None, "normalized": None, "status": "unranked_no_reviews"}
        cases = [
            ("winner removed", [row for row in rows if row is not winner]),
            ("all rows removed", []),
            ("row duplicated", rows + [copy.deepcopy(rows[0])]),
            ("fabricated unranked row", rows + [{"project_id": "prj_t2fake", **unranked}]),
            ("draft project row added", rows + [{"project_id": "prj_t2wip", **unranked, "status": "draft"}]),
        ]
        for label, tampered in cases:
            with self.subTest(label):
                result = self._verify_rows(tampered)
                self.assertEqual(result["verdict"], "differs")
                self.assertFalse(result["rows_match"])
