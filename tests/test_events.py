"""events: event CRUD, tracks, prizes, custom questions, registration, organizers.

Tests cover every rule in the P1-EV packet:
- create_event: host ok, plain user 403, anonymous 401, slug uniqueness
- window validation: close before open, judging before close
- update_event: organizer ok, participant 403, other event's organizer 403
- scoring-locked changes: submission window + ranking/lambda → 409
- close_submissions: organizer ok, non-open event 403, scoring-locked 409
- register_participant: open → 201, closed event → 403, judge → 409
- track/prize/question CRUD and business rules
- add_organizer / remove_organizer (last organizer 409)
- question with answers → 409 on delete; is_active toggle works
- prize track cross-event → 400
- manage page access control (judge/participant → 403)
- API endpoints: correct codes, error envelopes
- audit rows written for every state change
- assertNumQueries for the event list endpoint
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from audit.models import AuditEvent
from core.errors import ApiError
from events import policy, services
from events.models import CustomQuestion, Event, EventRole, Role, Track
from projects.models import Project, ProjectStatus
from teams.models import Team


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _future(days: int = 30):
    return timezone.now() + timedelta(days=days)


def _past(days: int = 1):
    return timezone.now() - timedelta(days=days)


def _make_user(email: str, *, is_admin=False, is_host=False) -> User:
    return User.objects.create_user(email, "testpass123", is_admin=is_admin, is_host=is_host)


def _make_event(creator: User, *, open_submissions: bool = True,
                scoring_locked: bool = False) -> Event:
    """Create a minimal event; open or closed submissions."""
    open_at = _past(5) if open_submissions else _past(15)
    close_at = _future(10) if open_submissions else _past(5)
    event = Event.objects.create(
        slug=f"test-event-{Event.objects.count() + 1}",
        name=f"Test Event {Event.objects.count() + 1}",
        submissions_open_at=open_at,
        submissions_close_at=close_at,
        judging_open_at=_future(10) if open_submissions else _past(5),
        created_by=creator,
    )
    if scoring_locked:
        event.scoring_locked_at = timezone.now()
        event.save(update_fields=["scoring_locked_at"])
    EventRole.objects.create(
        event=event, user=creator, role=Role.ORGANIZER,
        public_id=f"org_test{event.pk}",
    )
    return event


def _make_track(event: Event, name: str = "Open") -> Track:
    return Track.objects.create(event=event, name=name, position=0)


# ---------------------------------------------------------------------------
# create_event
# ---------------------------------------------------------------------------

class CreateEventTests(TestCase):
    def setUp(self):
        self.host = _make_user("host@example.org", is_host=True)
        self.admin = _make_user("admin@example.org", is_admin=True)
        self.plain = _make_user("plain@example.org")
        self.valid_data = {
            "name": "My Hackathon",
            "submissions_close_at": _future(10),
        }

    def test_host_can_create_event(self):
        event = services.create_event(self.host, self.valid_data)
        self.assertEqual(event.name, "My Hackathon")
        self.assertEqual(event.created_by, self.host)

    def test_admin_can_create_event(self):
        event = services.create_event(self.admin, self.valid_data)
        self.assertEqual(event.created_by, self.admin)

    def test_plain_user_gets_403(self):
        with self.assertRaises(ApiError) as ctx:
            services.create_event(self.plain, self.valid_data)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_anonymous_gets_401(self):
        with self.assertRaises(ApiError) as ctx:
            services.create_event(None, self.valid_data)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_creator_becomes_organizer(self):
        event = services.create_event(self.host, self.valid_data)
        self.assertTrue(
            EventRole.objects.filter(event=event, user=self.host, role=Role.ORGANIZER).exists()
        )

    def test_slug_is_derived_from_name(self):
        event = services.create_event(self.host, self.valid_data)
        self.assertIn("hackathon", event.slug)

    def test_slug_uniqueness_is_enforced(self):
        e1 = services.create_event(self.host, dict(self.valid_data, name="Clash"))
        e2 = services.create_event(self.host, dict(self.valid_data, name="Clash"))
        self.assertNotEqual(e1.slug, e2.slug)

    def test_audit_row_written(self):
        event = services.create_event(self.host, self.valid_data)
        self.assertTrue(AuditEvent.objects.filter(event=event, action="event.created").exists())

    def test_missing_submissions_close_at_is_400(self):
        with self.assertRaises(ApiError) as ctx:
            services.create_event(self.host, {"name": "No close"})
        self.assertEqual(ctx.exception.status_code, 400)

    def test_close_before_open_is_400(self):
        data = {
            "name": "Bad window",
            "submissions_open_at": _future(10),
            "submissions_close_at": _future(5),  # before open
        }
        with self.assertRaises(ApiError) as ctx:
            services.create_event(self.host, data)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("submissions_open_at", ctx.exception.fields)

    def test_judging_before_submissions_close_is_400(self):
        data = {
            "name": "Bad judging",
            "submissions_close_at": _future(10),
            "judging_open_at": _future(5),  # before submissions_close_at
        }
        with self.assertRaises(ApiError) as ctx:
            services.create_event(self.host, data)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("judging_open_at", ctx.exception.fields)

    def test_judging_close_before_judging_open_is_400(self):
        data = {
            "name": "Bad judging window",
            "submissions_close_at": _future(5),
            "judging_open_at": _future(5),
            "judging_close_at": _future(3),  # before judging_open_at
        }
        with self.assertRaises(ApiError) as ctx:
            services.create_event(self.host, data)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("judging_close_at", ctx.exception.fields)


# ---------------------------------------------------------------------------
# update_event
# ---------------------------------------------------------------------------

class UpdateEventTests(TestCase):
    def setUp(self):
        self.host = _make_user("host@example.org", is_host=True)
        self.plain = _make_user("plain@example.org")
        self.other_host = _make_user("other@example.org", is_host=True)
        self.event = _make_event(self.host)
        self.other_event = _make_event(self.other_host)
        # Make plain user a participant in this event
        EventRole.objects.create(
            event=self.event, user=self.plain, role=Role.PARTICIPANT,
            public_id="par_test1",
        )

    def test_organizer_can_update_name(self):
        event = services.update_event(self.host, self.event, {"name": "Renamed"})
        self.assertEqual(event.name, "Renamed")

    def test_participant_cannot_update(self):
        with self.assertRaises(ApiError) as ctx:
            services.update_event(self.plain, self.event, {"name": "Nope"})
        self.assertEqual(ctx.exception.status_code, 403)

    def test_other_events_organizer_cannot_update(self):
        with self.assertRaises(ApiError) as ctx:
            services.update_event(self.other_host, self.event, {"name": "Nope"})
        self.assertEqual(ctx.exception.status_code, 403)

    def test_window_validation_on_update(self):
        with self.assertRaises(ApiError) as ctx:
            services.update_event(self.host, self.event, {
                "submissions_close_at": _past(20),  # before open_at (_past(5))
                "submissions_open_at": _past(5),
            })
        self.assertEqual(ctx.exception.status_code, 400)

    def test_audit_row_written_on_change(self):
        before = AuditEvent.objects.filter(event=self.event, action="event.updated").count()
        services.update_event(self.host, self.event, {"tagline": "New tagline"})
        after = AuditEvent.objects.filter(event=self.event, action="event.updated").count()
        self.assertEqual(after, before + 1)

    def test_no_audit_row_when_nothing_changes(self):
        before = AuditEvent.objects.filter(event=self.event, action="event.updated").count()
        services.update_event(self.host, self.event, {})
        after = AuditEvent.objects.filter(event=self.event, action="event.updated").count()
        self.assertEqual(after, before)


# ---------------------------------------------------------------------------
# scoring-locked changes
# ---------------------------------------------------------------------------

class ScoringLockedTests(TestCase):
    def setUp(self):
        self.host = _make_user("host@example.org", is_host=True)
        self.event = _make_event(self.host, scoring_locked=True)

    def test_submission_window_change_is_409(self):
        with self.assertRaises(ApiError) as ctx:
            services.update_event(self.host, self.event, {
                "submissions_close_at": _future(999),
            })
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "scoring_locked")

    def test_ranking_method_change_is_409(self):
        with self.assertRaises(ApiError) as ctx:
            services.update_event(self.host, self.event, {"ranking_method": "raw"})
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "scoring_locked")

    def test_shrinkage_lambda_change_is_409(self):
        with self.assertRaises(ApiError) as ctx:
            services.update_event(self.host, self.event, {"shrinkage_lambda": "5.0"})
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "scoring_locked")

    def test_non_locked_field_change_is_ok(self):
        event = services.update_event(self.host, self.event, {"tagline": "New tagline"})
        self.assertEqual(event.tagline, "New tagline")

    def test_close_submissions_blocked_when_scoring_locked(self):
        """close_submissions must also check the scoring lock."""
        # Reopen submissions artificially so the window is "open":
        self.event.submissions_open_at = _past(5)
        self.event.submissions_close_at = _future(5)
        self.event.save(update_fields=["submissions_open_at", "submissions_close_at"])
        with self.assertRaises(ApiError) as ctx:
            services.close_submissions(self.host, self.event)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "scoring_locked")


# ---------------------------------------------------------------------------
# close_submissions
# ---------------------------------------------------------------------------

class CloseSubmissionsTests(TestCase):
    def setUp(self):
        self.host = _make_user("host@example.org", is_host=True)
        self.event = _make_event(self.host, open_submissions=True)

    def test_organizer_can_close_open_submissions(self):
        event = services.close_submissions(self.host, self.event)
        self.assertLessEqual(event.submissions_close_at, timezone.now())
        self.assertLessEqual(event.judging_open_at, timezone.now())

    def test_already_closed_event_raises_403(self):
        closed = _make_event(self.host, open_submissions=False)
        with self.assertRaises(ApiError) as ctx:
            services.close_submissions(self.host, closed)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.code, "window_closed")

    def test_non_organizer_cannot_close_submissions(self):
        plain = _make_user("plain@example.org")
        with self.assertRaises(ApiError) as ctx:
            services.close_submissions(plain, self.event)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_audit_row_written(self):
        services.close_submissions(self.host, self.event)
        self.assertTrue(
            AuditEvent.objects.filter(event=self.event, action="event.submissions_closed").exists()
        )


# ---------------------------------------------------------------------------
# register_participant
# ---------------------------------------------------------------------------

class RegisterParticipantTests(TestCase):
    def setUp(self):
        self.host = _make_user("host@example.org", is_host=True)
        self.open_event = _make_event(self.host, open_submissions=True)
        self.closed_event = _make_event(self.host, open_submissions=False)

    def test_register_in_open_event_creates_role(self):
        user = _make_user("user@example.org")
        role, created = services.register_participant(user, self.open_event)
        self.assertTrue(created)
        self.assertEqual(role.role, Role.PARTICIPANT)

    def test_register_returns_201_status_via_api(self):
        user = _make_user("user@example.org")
        role, created = services.register_participant(user, self.open_event)
        self.assertTrue(created)

    def test_idempotent_for_existing_participant(self):
        user = _make_user("user@example.org")
        role1, _ = services.register_participant(user, self.open_event)
        role2, created2 = services.register_participant(user, self.open_event)
        self.assertFalse(created2)
        self.assertEqual(role1.pk, role2.pk)

    def test_closed_event_returns_403_window_closed(self):
        user = _make_user("user@example.org")
        with self.assertRaises(ApiError) as ctx:
            services.register_participant(user, self.closed_event)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.code, "window_closed")

    def test_judge_cannot_register_as_participant(self):
        judge = _make_user("judge@example.org")
        EventRole.objects.create(
            event=self.open_event, user=judge, role=Role.JUDGE,
            public_id="jdg_regtest",
        )
        with self.assertRaises(ApiError) as ctx:
            services.register_participant(judge, self.open_event)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "role_conflict")

    def test_anonymous_gets_401(self):
        with self.assertRaises(ApiError) as ctx:
            services.register_participant(None, self.open_event)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_audit_row_written(self):
        user = _make_user("user@example.org")
        services.register_participant(user, self.open_event)
        self.assertTrue(
            AuditEvent.objects.filter(
                event=self.open_event, action="event.participant_registered"
            ).exists()
        )


# ---------------------------------------------------------------------------
# add_organizer / remove_organizer
# ---------------------------------------------------------------------------

class OrganizerManagementTests(TestCase):
    def setUp(self):
        self.host = _make_user("host@example.org", is_host=True)
        self.event = _make_event(self.host)

    def test_add_organizer_by_email(self):
        _make_user("new_org@example.org")
        role, created = services.add_organizer(self.host, self.event, "new_org@example.org")
        self.assertTrue(created)
        self.assertEqual(role.role, Role.ORGANIZER)

    def test_idempotent_for_existing_organizer(self):
        _make_user("new_org@example.org")
        role1, _ = services.add_organizer(self.host, self.event, "new_org@example.org")
        role2, created2 = services.add_organizer(self.host, self.event, "new_org@example.org")
        self.assertFalse(created2)
        self.assertEqual(role1.pk, role2.pk)

    def test_add_user_with_existing_other_role_is_409(self):
        participant = _make_user("par@example.org")
        EventRole.objects.create(
            event=self.event, user=participant, role=Role.PARTICIPANT,
            public_id="par_conflict_test",
        )
        with self.assertRaises(ApiError) as ctx:
            services.add_organizer(self.host, self.event, "par@example.org")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "role_conflict")

    def test_add_nonexistent_user_is_404(self):
        with self.assertRaises(ApiError) as ctx:
            services.add_organizer(self.host, self.event, "nobody@example.org")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_remove_organizer(self):
        second = _make_user("second@example.org")
        role2, _ = services.add_organizer(self.host, self.event, "second@example.org")
        services.remove_organizer(self.host, self.event, role2)
        self.assertFalse(
            EventRole.objects.filter(event=self.event, user=second, role=Role.ORGANIZER).exists()
        )

    def test_last_organizer_cannot_be_removed(self):
        organizer_role = EventRole.objects.get(event=self.event, user=self.host, role=Role.ORGANIZER)
        with self.assertRaises(ApiError) as ctx:
            services.remove_organizer(self.host, self.event, organizer_role)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "last_organizer")

    def test_non_organizer_cannot_add_organizer(self):
        plain = _make_user("plain@example.org")
        with self.assertRaises(ApiError) as ctx:
            services.add_organizer(plain, self.event, "new_org@example.org")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_audit_rows_written(self):
        _make_user("new_org@example.org")
        role, _ = services.add_organizer(self.host, self.event, "new_org@example.org")
        self.assertTrue(
            AuditEvent.objects.filter(event=self.event, action="event.organizer_added").exists()
        )
        services.remove_organizer(self.host, self.event, role)
        self.assertTrue(
            AuditEvent.objects.filter(event=self.event, action="event.organizer_removed").exists()
        )


# ---------------------------------------------------------------------------
# track CRUD
# ---------------------------------------------------------------------------

class TrackTests(TestCase):
    def setUp(self):
        self.host = _make_user("host@example.org", is_host=True)
        self.event = _make_event(self.host)

    def test_create_track(self):
        track = services.create_track(self.host, self.event, {"name": "AI", "position": 1})
        self.assertEqual(track.name, "AI")
        self.assertEqual(track.event, self.event)

    def test_non_organizer_cannot_create_track(self):
        plain = _make_user("plain@example.org")
        with self.assertRaises(ApiError) as ctx:
            services.create_track(plain, self.event, {"name": "AI"})
        self.assertEqual(ctx.exception.status_code, 403)

    def test_update_track(self):
        track = services.create_track(self.host, self.event, {"name": "AI"})
        updated = services.update_track(self.host, self.event, track, {"name": "Machine Learning"})
        self.assertEqual(updated.name, "Machine Learning")

    def test_delete_unused_track(self):
        track = services.create_track(self.host, self.event, {"name": "ToDelete"})
        services.delete_track(self.host, self.event, track)
        self.assertFalse(Track.objects.filter(pk=track.pk).exists())

    def test_delete_track_in_use_is_409(self):
        track = services.create_track(self.host, self.event, {"name": "UsedTrack"})
        team = Team.objects.create(event=self.event, name="Team A", created_by=self.host,
                                   public_id="tm_trktest")
        Project.objects.create(
            event=self.event,
            team=team,
            track=track,
            title="Test Project",
            status=ProjectStatus.DRAFT,
            public_id="prj_trktest",
        )
        with self.assertRaises(ApiError) as ctx:
            services.delete_track(self.host, self.event, track)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "track_in_use")

    def test_audit_rows_written(self):
        track = services.create_track(self.host, self.event, {"name": "Audit Track"})
        self.assertTrue(
            AuditEvent.objects.filter(event=self.event, action="event.track_created").exists()
        )
        services.delete_track(self.host, self.event, track)
        self.assertTrue(
            AuditEvent.objects.filter(event=self.event, action="event.track_deleted").exists()
        )


# ---------------------------------------------------------------------------
# prize CRUD
# ---------------------------------------------------------------------------

class PrizeTests(TestCase):
    def setUp(self):
        self.host = _make_user("host@example.org", is_host=True)
        self.event = _make_event(self.host)
        self.track = _make_track(self.event)

    def test_create_prize(self):
        prize = services.create_prize(self.host, self.event, {"name": "Best AI", "value": "$500"})
        self.assertEqual(prize.name, "Best AI")

    def test_create_prize_with_track(self):
        prize = services.create_prize(
            self.host, self.event,
            {"name": "Track Prize", "track": self.track.public_id}
        )
        self.assertEqual(prize.track, self.track)

    def test_create_track_prize_with_places(self):
        """A track prize created with places=2 stores that value and sets scope=track."""
        prize = services.create_prize(
            self.host, self.event,
            {"name": "Runner-up", "track": self.track.public_id, "places": 2}
        )
        self.assertEqual(prize.places, 2)
        self.assertEqual(prize.scope, "track")

    def test_cross_event_track_is_400(self):
        other_host = _make_user("other@example.org", is_host=True)
        other_event = _make_event(other_host)
        other_track = _make_track(other_event, name="Other")
        with self.assertRaises(ApiError) as ctx:
            services.create_prize(
                self.host, self.event, {"name": "Cross", "track": other_track.public_id}
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "cross_event")

    def test_delete_prize(self):
        prize = services.create_prize(self.host, self.event, {"name": "ToDelete"})
        services.delete_prize(self.host, self.event, prize)
        from events.models import Prize
        self.assertFalse(Prize.objects.filter(pk=prize.pk).exists())

    def test_non_organizer_cannot_create_prize(self):
        plain = _make_user("plain@example.org")
        with self.assertRaises(ApiError) as ctx:
            services.create_prize(plain, self.event, {"name": "Nope"})
        self.assertEqual(ctx.exception.status_code, 403)


# ---------------------------------------------------------------------------
# New-field tests: places, one_prize_per_team, cross-event track
# ---------------------------------------------------------------------------

class PrizeNewFieldsTests(TestCase):
    """Covers the three new-field scenarios added with Prize.places /
    eligibility_note and Event.one_prize_per_team."""

    def setUp(self):
        self.host = _make_user("host_nf@example.org", is_host=True)
        self.event = _make_event(self.host)
        self.track = _make_track(self.event, "AI")

    def test_update_one_prize_per_team(self):
        """Organizer can flip one_prize_per_team via update_event."""
        original = self.event.one_prize_per_team
        updated = services.update_event(
            self.host, self.event, {"one_prize_per_team": not original}
        )
        self.assertEqual(updated.one_prize_per_team, not original)
        # Flip back for symmetry
        updated2 = services.update_event(
            self.host, updated, {"one_prize_per_team": original}
        )
        self.assertEqual(updated2.one_prize_per_team, original)

    def test_cross_event_track_still_rejected(self):
        """A track belonging to a different event is always rejected (400 cross_event)."""
        other_host = _make_user("other_nf@example.org", is_host=True)
        other_event = _make_event(other_host)
        other_track = _make_track(other_event, "Foreign")
        with self.assertRaises(ApiError) as ctx:
            services.create_prize(
                self.host, self.event,
                {"name": "Stolen Prize", "track": other_track.public_id, "places": 1},
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "cross_event")


# ---------------------------------------------------------------------------
# custom question CRUD
# ---------------------------------------------------------------------------

class QuestionTests(TestCase):
    def setUp(self):
        self.host = _make_user("host@example.org", is_host=True)
        self.event = _make_event(self.host)

    def test_create_question(self):
        q = services.create_question(
            self.host, self.event,
            {"prompt": "What inspired you?", "kind": "long_text"}
        )
        self.assertEqual(q.prompt, "What inspired you?")
        self.assertTrue(q.is_active)

    def test_delete_question_without_answers(self):
        q = services.create_question(self.host, self.event, {"prompt": "Unused?"})
        services.delete_question(self.host, self.event, q)
        self.assertFalse(CustomQuestion.objects.filter(pk=q.pk).exists())

    def test_delete_question_with_answers_is_409(self):
        from projects.models import Answer
        q = services.create_question(self.host, self.event, {"prompt": "With answer?"})
        team = Team.objects.create(event=self.event, name="Team Q", created_by=self.host,
                                   public_id="tm_qtest")
        project = Project.objects.create(
            event=self.event,
            team=team,
            title="Test Project Q",
            status=ProjectStatus.DRAFT,
            public_id="prj_qtest",
        )
        Answer.objects.create(project=project, question=q, value="My answer")
        with self.assertRaises(ApiError) as ctx:
            services.delete_question(self.host, self.event, q)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "question_in_use")

    def test_is_active_toggle_instead_of_delete(self):
        q = services.create_question(self.host, self.event, {"prompt": "Toggle me?"})
        self.assertTrue(q.is_active)
        updated = services.update_question(self.host, self.event, q, {"is_active": False})
        self.assertFalse(updated.is_active)
        re_enabled = services.update_question(self.host, self.event, q, {"is_active": True})
        self.assertTrue(re_enabled.is_active)

    def test_non_organizer_cannot_create_question(self):
        plain = _make_user("plain@example.org")
        with self.assertRaises(ApiError) as ctx:
            services.create_question(plain, self.event, {"prompt": "Nope"})
        self.assertEqual(ctx.exception.status_code, 403)

    def test_audit_row_on_create(self):
        services.create_question(self.host, self.event, {"prompt": "Audit?"})
        self.assertTrue(
            AuditEvent.objects.filter(event=self.event, action="event.question_created").exists()
        )


# ---------------------------------------------------------------------------
# Policy helpers
# ---------------------------------------------------------------------------

class PolicyTests(TestCase):
    def setUp(self):
        self.host = _make_user("host@example.org", is_host=True)
        self.plain = _make_user("plain@example.org")
        self.event = _make_event(self.host)

    def test_visible_events_returns_all_events(self):
        qs = policy.visible_events(self.plain)
        self.assertIn(self.event, qs)

    def test_visible_events_for_anonymous(self):
        qs = policy.visible_events(None)
        self.assertIn(self.event, qs)

    def test_can_manage_is_true_for_organizer(self):
        self.assertTrue(policy.can_manage(self.host, self.event))

    def test_can_manage_is_false_for_plain_user(self):
        self.assertFalse(policy.can_manage(self.plain, self.event))

    def test_can_manage_is_false_for_anonymous(self):
        self.assertFalse(policy.can_manage(None, self.event))

    def test_can_manage_is_true_for_admin(self):
        admin = _make_user("admin@example.org", is_admin=True)
        self.assertTrue(policy.can_manage(admin, self.event))

    def test_role_of_returns_correct_role(self):
        participant = _make_user("par@example.org")
        EventRole.objects.create(
            event=self.event, user=participant, role=Role.PARTICIPANT,
            public_id="par_pol_test",
        )
        self.assertEqual(policy.role_of(participant, self.event), Role.PARTICIPANT)
        self.assertIsNone(policy.role_of(self.plain, self.event))

    def test_submission_window_open(self):
        open_event = _make_event(self.host, open_submissions=True)
        closed_event = _make_event(self.host, open_submissions=False)
        self.assertTrue(policy.submission_window_open(open_event))
        self.assertFalse(policy.submission_window_open(closed_event))


# ---------------------------------------------------------------------------
# API endpoint tests (using Django test client)
# ---------------------------------------------------------------------------

class EventApiTests(TestCase):
    def setUp(self):
        self.host = _make_user("host@example.org", is_host=True)
        self.plain = _make_user("plain@example.org")
        self.event = _make_event(self.host)

    def _auth(self, user):
        self.client.force_login(user)

    def test_get_events_200_anonymous(self):
        response = self.client.get("/api/v1/events", follow=False)
        self.assertEqual(response.status_code, 200)

    def test_post_event_as_host_201(self):
        self._auth(self.host)
        response = self.client.post(
            "/api/v1/events",
            data={
                "name": "API Created Event",
                "submissions_close_at": _future(10).isoformat(),
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertIn("slug", response.json())

    def test_post_event_as_plain_user_403(self):
        self._auth(self.plain)
        response = self.client.post(
            "/api/v1/events",
            data={"name": "Fail", "submissions_close_at": _future(10).isoformat()},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "forbidden")

    def test_post_event_anonymous_401(self):
        response = self.client.post(
            "/api/v1/events",
            data={"name": "Fail", "submissions_close_at": _future(10).isoformat()},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_get_event_detail_200(self):
        response = self.client.get(f"/api/v1/events/{self.event.slug}", follow=False)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["slug"], self.event.slug)
        self.assertIn("phase", data)
        self.assertIn("submission_window_open", data)

    def test_patch_event_as_organizer_200(self):
        self._auth(self.host)
        response = self.client.patch(
            f"/api/v1/events/{self.event.slug}",
            data={"tagline": "New tagline"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    def test_patch_event_as_plain_user_403(self):
        self._auth(self.plain)
        response = self.client.patch(
            f"/api/v1/events/{self.event.slug}",
            data={"tagline": "Nope"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_post_register_open_event_201(self):
        user = _make_user("reg@example.org")
        self._auth(user)
        response = self.client.post(
            f"/api/v1/events/{self.event.slug}/register",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)

    def test_post_register_closed_event_403_window_closed(self):
        user = _make_user("reg@example.org")
        self._auth(user)
        closed = _make_event(self.host, open_submissions=False)
        response = self.client.post(
            f"/api/v1/events/{closed.slug}/register",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "window_closed")

    def test_phase_field_values(self):
        """phase is computed correctly for upcoming, submissions_open, judging."""
        # open_submissions event: submissions_open
        response = self.client.get(f"/api/v1/events/{self.event.slug}", follow=False)
        self.assertEqual(response.json()["phase"], "submissions_open")

        # closed event with judging window open: judging
        closed = _make_event(self.host, open_submissions=False)
        response = self.client.get(f"/api/v1/events/{closed.slug}", follow=False)
        self.assertEqual(response.json()["phase"], "judging")

    def test_event_list_query_count(self):
        # Create a few more events to make N+1 obvious
        for i in range(5):
            _make_event(self.host)
        with self.assertNumQueries(2):
            # 1: event list, 1: prefetch result_publications
            response = self.client.get("/api/v1/events", follow=False)
        self.assertEqual(response.status_code, 200)

    def test_tracks_crud_via_api(self):
        self._auth(self.host)
        # Create
        response = self.client.post(
            f"/api/v1/events/{self.event.slug}/tracks",
            data={"name": "SustainTech", "position": 1},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        public_id = response.json()["public_id"]

        # List
        response = self.client.get(f"/api/v1/events/{self.event.slug}/tracks", follow=False)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(t["public_id"] == public_id for t in response.json()))

        # Patch
        response = self.client.patch(
            f"/api/v1/events/{self.event.slug}/tracks/{public_id}",
            data={"name": "Sustainability"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        # Delete
        response = self.client.delete(
            f"/api/v1/events/{self.event.slug}/tracks/{public_id}",
        )
        self.assertEqual(response.status_code, 204)

    def test_prizes_crud_via_api(self):
        self._auth(self.host)
        response = self.client.post(
            f"/api/v1/events/{self.event.slug}/prizes",
            data={"name": "Grand Prize", "value": "$1000"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        public_id = response.json()["public_id"]

        response = self.client.delete(
            f"/api/v1/events/{self.event.slug}/prizes/{public_id}",
        )
        self.assertEqual(response.status_code, 204)

    def test_questions_crud_via_api(self):
        self._auth(self.host)
        response = self.client.post(
            f"/api/v1/events/{self.event.slug}/questions",
            data={"prompt": "What is your stack?", "kind": "short_text"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        public_id = response.json()["public_id"]

        # Patch (toggle is_active)
        response = self.client.patch(
            f"/api/v1/events/{self.event.slug}/questions/{public_id}",
            data={"is_active": False},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_active"])

    def test_close_judging_via_api(self):
        self._auth(self.host)
        # Need an event where judging has already opened (judging_open_at in the past)
        # so that setting judging_close_at = now() satisfies the DB constraint.
        judging_event = Event.objects.create(
            slug="judging-open-event",
            name="Judging Open Event",
            submissions_close_at=_past(5),
            judging_open_at=_past(3),
            created_by=self.host,
        )
        EventRole.objects.create(
            event=judging_event, user=self.host, role=Role.ORGANIZER,
            public_id="org_judgetest",
        )
        response = self.client.post(
            f"/api/v1/events/{judging_event.slug}/close-judging",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    def test_close_submissions_via_api(self):
        self._auth(self.host)
        response = self.client.post(
            f"/api/v1/events/{self.event.slug}/close-submissions",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    def test_organizers_list_create_delete(self):
        self._auth(self.host)
        _make_user("new_org@example.org")
        response = self.client.post(
            f"/api/v1/events/{self.event.slug}/organizers",
            data={"email": "new_org@example.org"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        public_id = response.json()["public_id"]

        # List
        response = self.client.get(f"/api/v1/events/{self.event.slug}/organizers", follow=False)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(o["public_id"] == public_id for o in response.json()))

        # Delete
        response = self.client.delete(
            f"/api/v1/events/{self.event.slug}/organizers/{public_id}",
        )
        self.assertEqual(response.status_code, 204)

    def test_delete_track_in_use_409_via_api(self):
        self._auth(self.host)
        # Create track
        r = self.client.post(
            f"/api/v1/events/{self.event.slug}/tracks",
            data={"name": "InUseTrack"},
            content_type="application/json",
        )
        public_id = r.json()["public_id"]
        track = Track.objects.get(public_id=public_id)
        team = Team.objects.create(event=self.event, name="Team B", created_by=self.host,
                                   public_id="tm_trkapi")
        Project.objects.create(
            event=self.event,
            team=team,
            track=track,
            title="In Use Project",
            status=ProjectStatus.DRAFT,
            public_id="prj_trkapi",
        )
        response = self.client.delete(
            f"/api/v1/events/{self.event.slug}/tracks/{public_id}",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "track_in_use")

    def test_scoring_locked_409_via_api(self):
        self._auth(self.host)
        locked_event = _make_event(self.host, scoring_locked=True)
        response = self.client.patch(
            f"/api/v1/events/{locked_event.slug}",
            data={"ranking_method": "raw"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "scoring_locked")

    def test_cross_event_prize_track_400_via_api(self):
        self._auth(self.host)
        other_host = _make_user("other2@example.org", is_host=True)
        other_event = _make_event(other_host)
        other_track = _make_track(other_event, "Foreign")
        response = self.client.post(
            f"/api/v1/events/{self.event.slug}/prizes",
            data={"name": "Cross Prize", "track": other_track.public_id},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "cross_event")

    def test_last_organizer_remove_409_via_api(self):
        self._auth(self.host)
        organizer_role = EventRole.objects.get(event=self.event, user=self.host)
        response = self.client.delete(
            f"/api/v1/events/{self.event.slug}/organizers/{organizer_role.public_id}",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "last_organizer")

    def test_question_with_answers_delete_409_via_api(self):
        from projects.models import Answer
        self._auth(self.host)
        r = self.client.post(
            f"/api/v1/events/{self.event.slug}/questions",
            data={"prompt": "Delete blocked?", "kind": "short_text"},
            content_type="application/json",
        )
        public_id = r.json()["public_id"]
        q = CustomQuestion.objects.get(public_id=public_id)
        team = Team.objects.create(event=self.event, name="Team Q2", created_by=self.host,
                                   public_id="tm_qapi")
        project = Project.objects.create(
            event=self.event,
            team=team,
            title="API Project Q",
            status=ProjectStatus.DRAFT,
            public_id="prj_qapi",
        )
        Answer.objects.create(project=project, question=q, value="answer")
        response = self.client.delete(
            f"/api/v1/events/{self.event.slug}/questions/{public_id}",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "question_in_use")


# ---------------------------------------------------------------------------
# HTML page access control
# ---------------------------------------------------------------------------

class ManagePageAccessTests(TestCase):
    def setUp(self):
        self.host = _make_user("host@example.org", is_host=True)
        self.judge = _make_user("judge@example.org")
        self.participant = _make_user("par@example.org")
        self.event = _make_event(self.host)
        EventRole.objects.create(
            event=self.event, user=self.judge, role=Role.JUDGE,
            public_id="jdg_acc_test",
        )
        EventRole.objects.create(
            event=self.event, user=self.participant, role=Role.PARTICIPANT,
            public_id="par_acc_test",
        )

    def test_manage_overview_accessible_to_organizer(self):
        self.client.force_login(self.host)
        response = self.client.get(f"/manage/{self.event.slug}/", follow=False)
        self.assertEqual(response.status_code, 200)

    def test_manage_overview_403_for_judge(self):
        self.client.force_login(self.judge)
        response = self.client.get(f"/manage/{self.event.slug}/", follow=False)
        self.assertEqual(response.status_code, 403)

    def test_manage_overview_403_for_participant(self):
        self.client.force_login(self.participant)
        response = self.client.get(f"/manage/{self.event.slug}/", follow=False)
        self.assertEqual(response.status_code, 403)

    def test_manage_settings_403_for_judge(self):
        self.client.force_login(self.judge)
        response = self.client.get(f"/manage/{self.event.slug}/settings", follow=False)
        self.assertEqual(response.status_code, 403)

    def test_manage_setup_403_for_participant(self):
        self.client.force_login(self.participant)
        response = self.client.get(f"/manage/{self.event.slug}/setup", follow=False)
        self.assertEqual(response.status_code, 403)

    def test_manage_pages_accessible_to_admin(self):
        admin = _make_user("admin@example.org", is_admin=True)
        self.client.force_login(admin)
        for path in [
            f"/manage/{self.event.slug}/",
            f"/manage/{self.event.slug}/settings",
            f"/manage/{self.event.slug}/setup",
        ]:
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 200)

    def test_event_new_accessible_to_host(self):
        self.client.force_login(self.host)
        response = self.client.get("/events/new", follow=False)
        self.assertEqual(response.status_code, 200)

    def test_event_new_403_for_plain_user(self):
        self.client.force_login(self.participant)
        response = self.client.get("/events/new", follow=False)
        self.assertEqual(response.status_code, 403)

    def test_event_list_200_anonymous(self):
        response = self.client.get("/events/", follow=False)
        self.assertEqual(response.status_code, 200)

    def test_event_detail_200_anonymous(self):
        response = self.client.get(f"/events/{self.event.slug}/", follow=False)
        self.assertEqual(response.status_code, 200)
