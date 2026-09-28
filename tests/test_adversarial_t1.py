"""Adversarial T1: invite and roster races, and the exact instant the window shuts.

The races run on real PostgreSQL connections. The test transaction holds the
rows a service needs, pg_blocking_pids proves each worker is queued behind
those locks (a bare sleep would prove nothing), the test changes the world or
leaves it alone, and then lets go. A worker must decide on what is true after
its wait, never on what it read before it. Every race also has a winner, so a
service that refused everything would fail here as well.
"""
import contextlib
import threading
import time
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest import mock

from accounts.models import User
from audit.models import AuditEvent
from core.clock import now
from core.errors import ApiError
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from events.models import Event
from teams import services
from teams.models import Team, TeamInvite, TeamMember

WAIT = 15  # seconds; every wait is bounded, so a regression fails instead of hanging
T0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime_timezone.utc)
MICROSECOND = timedelta(microseconds=1)


def make_user(email: str) -> User:
    return User.objects.create_user(email, "verdict-demo", display_name=email.split("@")[0])


def make_event(slug: str, close_at, organizer: User) -> Event:
    return Event.objects.create(
        slug=slug, name=slug.replace("-", " ").title(), submissions_open_at=None,
        submissions_close_at=close_at, judging_open_at=None, judging_close_at=None,
        max_team_size=4, gallery_public=True, created_by=organizer,
    )


def make_team(event: Event, owner: User, name: str) -> Team:
    team = Team.objects.create(event=event, name=name, created_by=owner)
    TeamMember.objects.create(team=team, user=owner, event=event, is_owner=True)
    return team


def make_invite(team: Team, **fields) -> TeamInvite:
    return TeamInvite.objects.create(team=team, created_by=team.created_by,
                                     expires_at=team.event.submissions_close_at, **fields)


@contextlib.contextmanager
def frozen(moment):
    """Pin every reader of the clock; the same list tests/test_deadlines.py patches."""
    with mock.patch("core.clock.now", return_value=moment), \
         mock.patch("events.policy.now", return_value=moment), \
         mock.patch("projects.services.now", return_value=moment), \
         mock.patch("teams.services.now", return_value=moment):
        yield moment


class Worker(threading.Thread):
    """One service call on its own connection. The outcome is kept, never raised."""

    def __init__(self, call):
        super().__init__(daemon=True)
        self.call = call
        self.connected = threading.Event()
        self.pid = self.result = self.error = None

    def run(self):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = 30000")  # milliseconds
                cursor.execute("SELECT pg_backend_pid()")
                self.pid = cursor.fetchone()[0]
            self.connected.set()
            self.result = self.call()
        except Exception as exc:  # judged on the main thread
            self.error = exc
        finally:
            self.connected.set()
            connection.close()

    def outcome(self) -> str:
        return repr(self.error) if self.error is not None else f"ok {self.result!r}"


class RaceCase(TransactionTestCase):
    def wait_until_queued(self, worker: Worker) -> None:
        """Return once PostgreSQL itself reports the worker waiting on another transaction."""
        self.assertTrue(worker.connected.wait(WAIT), "the worker never connected")
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline:
            if worker.pid is not None:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT cardinality(pg_blocking_pids(%s))", [worker.pid])
                    if cursor.fetchone()[0]:
                        return
            if not worker.is_alive():
                self.fail(f"the worker finished without waiting: {worker.outcome()}")
            time.sleep(0.01)  # a polling interval; the proof is pg_blocking_pids
        self.fail("the worker never queued behind a lock")

    def race(self, hold, calls, meanwhile=None) -> list[Worker]:
        """Hold rows, prove every call queues behind them, run meanwhile, then release."""
        workers = [Worker(call) for call in calls]
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = 15000")  # milliseconds
                hold()
                for worker in workers:
                    worker.start()
                    self.wait_until_queued(worker)
                if meanwhile is not None:
                    meanwhile()
        finally:
            for worker in workers:
                if worker.ident is not None:
                    worker.join(WAIT)
        self.assertEqual([w for w in workers if w.is_alive()], [], "a worker is still waiting")
        return workers

    def only_winner(self, workers: list[Worker]) -> tuple[Worker, Worker]:
        """Exactly one call succeeds; the other one is handed back for its refusal."""
        winners = [w for w in workers if w.error is None]
        self.assertEqual(len(winners), 1, [w.outcome() for w in workers])
        [loser] = [w for w in workers if w.error is not None]
        return winners[0], loser

    def assert_refused(self, worker: Worker, code: str, status: int) -> None:
        self.assertIsInstance(worker.error, ApiError, f"expected {code}, got {worker.outcome()}")
        self.assertEqual((worker.error.code, worker.error.status_code), (code, status))


class InviteRaceTests(RaceCase):
    def setUp(self):
        self.owner = make_user("owner@example.org")
        self.joiner = make_user("joiner@example.org")
        self.event = make_event("race-hack", now() + timedelta(hours=6), self.owner)
        self.team = make_team(self.event, self.owner, "North Kiln")

    def hold_event_and_team(self):
        # The order the services take: the event, then the team.
        Event.objects.select_for_update().get(pk=self.event.pk)
        Team.objects.select_for_update().get(pk=self.team.pk)

    def accept(self, user, invite):
        return lambda: services.accept_invite(user, invite.token)

    def test_control_an_accept_that_waited_on_an_unchanged_team_joins(self):
        invite = make_invite(self.team)
        [worker] = self.race(self.hold_event_and_team, [self.accept(self.joiner, invite)])
        self.assertIsNone(worker.error, worker.outcome())
        self.assertEqual(worker.result.user_id, self.joiner.pk)
        invite.refresh_from_db()
        self.assertEqual(invite.use_count, 1)

    def test_a_link_rotated_while_the_accept_waited_is_refused(self):
        invite = make_invite(self.team)
        [worker] = self.race(self.hold_event_and_team, [self.accept(self.joiner, invite)],
                             meanwhile=lambda: services.rotate_invite(self.owner, self.team))
        self.assert_refused(worker, "invite_invalid", 410)
        self.assertFalse(TeamMember.objects.filter(user=self.joiner).exists())
        invite.refresh_from_db()
        self.assertIsNotNone(invite.revoked_at)
        self.assertEqual(invite.use_count, 0)

    def test_a_single_use_link_admits_one_of_two_racing_strangers(self):
        invite = make_invite(self.team, max_uses=1)
        second = make_user("second@example.org")
        workers = self.race(self.hold_event_and_team,
                            [self.accept(self.joiner, invite), self.accept(second, invite)])
        _, loser = self.only_winner(workers)
        self.assert_refused(loser, "invite_invalid", 410)
        self.assertEqual(self.team.memberships.count(), 2)  # the owner and one stranger
        invite.refresh_from_db()
        self.assertEqual(invite.use_count, 1)

    def test_one_person_accepting_two_teams_at_once_lands_in_exactly_one(self):
        rival = make_team(self.event, make_user("rival@example.org"), "South Kiln")
        invites = [make_invite(self.team), make_invite(rival)]
        pks = [invite.pk for invite in invites]

        def hold_both_invites():
            list(TeamInvite.objects.select_for_update().filter(pk__in=pks))

        workers = self.race(hold_both_invites, [self.accept(self.joiner, i) for i in invites])
        _, loser = self.only_winner(workers)
        self.assert_refused(loser, "already_in_team", 409)
        self.assertEqual(TeamMember.objects.filter(event=self.event, user=self.joiner).count(), 1)
        self.assertEqual(sorted(TeamInvite.objects.filter(pk__in=pks)
                                .values_list("use_count", flat=True)), [0, 1])


class FrozenRosterTests(TestCase):
    """At submissions_close_at the roster and its links freeze; a microsecond before, not yet."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user("owner@example.org")
        cls.member = make_user("member@example.org")
        cls.event = make_event("frozen-hack", T0, cls.owner)
        cls.team = make_team(cls.event, cls.owner, "North Kiln")
        TeamMember.objects.create(team=cls.team, user=cls.member, event=cls.event)
        cls.invite = make_invite(cls.team)

    def snapshot(self):
        return (list(TeamMember.objects.order_by("pk").values_list("pk", flat=True)),
                list(TeamInvite.objects.order_by("pk").values_list("pk", "revoked_at")),
                AuditEvent.objects.count())

    def assert_refused_without_writes(self, call) -> None:
        before = self.snapshot()
        with frozen(T0), self.assertRaises(ApiError) as caught:
            call()
        self.assertEqual((caught.exception.code, caught.exception.status_code),
                         ("window_closed", 403))
        self.assertEqual(self.snapshot(), before)

    def test_removing_a_member_exactly_at_the_close_is_refused(self):
        self.assert_refused_without_writes(
            lambda: services.remove_member(self.owner, self.team, self.member))

    def test_control_removing_a_member_a_microsecond_before_the_close_works(self):
        with frozen(T0 - MICROSECOND):
            services.remove_member(self.owner, self.team, self.member)
        self.assertFalse(TeamMember.objects.filter(user=self.member).exists())
        self.assertEqual(AuditEvent.objects.filter(action="team.member_removed").count(), 1)

    def test_rotating_the_link_exactly_at_the_close_mints_nothing(self):
        self.assert_refused_without_writes(lambda: services.rotate_invite(self.owner, self.team))

    def test_control_rotating_a_microsecond_before_the_close_mints_a_link(self):
        with frozen(T0 - MICROSECOND):
            fresh = services.rotate_invite(self.owner, self.team)
        self.assertEqual(fresh.expires_at, T0)
        self.invite.refresh_from_db()
        self.assertIsNotNone(self.invite.revoked_at)
