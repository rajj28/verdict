"""Regressions for production credentials, PostgreSQL locks and publication gates."""
import os
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.db.backends.postgresql.base import DatabaseWrapper
from django.db.models.query import QuerySet
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import ApiToken, PasswordResetToken, User
from accounts.services import authenticate
from accounts.tokens import authenticate_token, issue_token
from audit.models import AuditEvent
from community.models import VotingConfig
from core.bootstrap import DEMO_PASSWORD, DEMO_TOKENS, banner, bootstrap
from core.errors import ApiError
from events.models import Event, EventRole, Role, Track
from judging.models import Criterion, Rubric
from projects import services as project_services
from projects.models import Project, ProjectStatus
from results import services as result_services
from results.models import ResultPublication
from teams.models import Team, TeamMember


class ProductionTransitionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with override_settings(DEMO_MODE=True):
            bootstrap()

    def setUp(self):
        self.production = override_settings(DEMO_MODE=False)
        self.production.enable()
        self.addCleanup(self.production.disable)
        self.environment = patch.dict(os.environ, {
            "ADMIN_EMAIL": "operator@production.test",
            "ADMIN_PASSWORD": "operator-first-secure-password",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_demo_admin_api_exploit_is_denied_before_bootstrap(self):
        response = self.client.get(
            "/api/v1/admin/users",
            HTTP_AUTHORIZATION="Bearer vd_demo_admin_c0ffee5eed01",
        )
        self.assertEqual(response.status_code, 401)
        for _name, _email, plaintext in DEMO_TOKENS:
            self.assertIsNone(authenticate_token(plaintext))

    def test_known_demo_token_is_denied_even_if_marker_was_removed(self):
        ApiToken.objects.update(is_demo=False)
        self.assertIsNone(authenticate_token("vd_demo_admin_c0ffee5eed01"))

    def test_random_demo_token_is_denied_by_its_marker(self):
        admin = User.objects.get(email="admin@verdict.local")
        _token, plaintext = issue_token(admin, "temporary demo", is_demo=True)
        self.assertIsNone(authenticate_token(plaintext))

    def test_demo_password_is_denied_before_bootstrap(self):
        self.assertIsNone(authenticate("admin@verdict.local", DEMO_PASSWORD))

    def test_existing_volume_retires_credentials_and_sessions(self):
        admin = User.objects.get(email="admin@verdict.local")
        self.client.force_login(admin)
        extra_token, plaintext = issue_token(admin, "minted using demo access")
        bootstrap()
        admin.refresh_from_db()
        self.assertFalse(admin.has_usable_password())
        self.assertFalse(ApiToken.objects.filter(is_demo=True, revoked_at__isnull=True).exists())
        extra_token.refresh_from_db()
        self.assertIsNotNone(extra_token.revoked_at)
        self.assertIsNone(authenticate_token(plaintext))
        self.assertEqual(self.client.get("/api/v1/admin/users").status_code, 401)
        for user in User.objects.all():
            self.assertFalse(user.check_password(DEMO_PASSWORD))
        operator = User.objects.get(email="operator@production.test")
        self.assertTrue(operator.is_admin)
        self.assertTrue(operator.check_password("operator-first-secure-password"))
        self.client.force_login(operator)
        self.assertEqual(self.client.get("/api/v1/admin/users").status_code, 200)

    def test_changed_credentials_sessions_and_tokens_survive_repeated_boot(self):
        admin = User.objects.get(email="admin@verdict.local")
        admin.set_password("legitimate-operator-password")
        admin.save(update_fields=["password"])
        saved_hash = admin.password
        _token, plaintext = issue_token(admin, "legitimate automation")
        self.assertEqual(self.client.post("/api/v1/auth/login", {
            "email": admin.email, "password": "legitimate-operator-password",
        }, content_type="application/json").status_code, 200)
        with patch.dict(os.environ, {"ADMIN_EMAIL": admin.email, "ADMIN_PASSWORD": "stale-env-value"}):
            bootstrap()
            hashes = dict(User.objects.values_list("pk", "password"))
            audit_count = AuditEvent.objects.count()
            bootstrap()
        admin.refresh_from_db()
        self.assertEqual(admin.password, saved_hash)
        self.assertEqual(dict(User.objects.values_list("pk", "password")), hashes)
        self.assertEqual(AuditEvent.objects.count(), audit_count)
        self.assertEqual(authenticate_token(plaintext), admin)
        self.assertEqual(self.client.get("/api/v1/admin/users").status_code, 200)

    def test_existing_safe_admin_does_not_require_environment_credentials(self):
        admin = User.objects.get(email="admin@verdict.local")
        admin.set_password("changed-by-real-operator")
        admin.save(update_fields=["password"])
        with patch.dict(os.environ, {"ADMIN_EMAIL": "", "ADMIN_PASSWORD": ""}):
            bootstrap()
        admin.refresh_from_db()
        self.assertTrue(admin.check_password("changed-by-real-operator"))

    def test_same_demo_admin_address_can_receive_explicit_production_password(self):
        with patch.dict(os.environ, {"ADMIN_EMAIL": " ADMIN@VERDICT.LOCAL "}):
            bootstrap()
        admin = User.objects.get(email="admin@verdict.local")
        self.assertTrue(admin.check_password("operator-first-secure-password"))

    def test_provisioning_without_both_credentials_fails_closed(self):
        for email, password in (("", ""), ("operator@production.test", ""), ("", "new-password")):
            with self.subTest(email=email), patch.dict(os.environ, {
                "ADMIN_EMAIL": email, "ADMIN_PASSWORD": password,
            }):
                with self.assertRaises(ImproperlyConfigured):
                    bootstrap()

    def test_known_demo_password_cannot_be_production_admin_password(self):
        with patch.dict(os.environ, {"ADMIN_PASSWORD": DEMO_PASSWORD}):
            with self.assertRaises(ImproperlyConfigured):
                bootstrap()

    def test_disabled_changed_account_is_not_reactivated_in_production(self):
        organizer = User.objects.get(email="organizer@verdict.local")
        organizer.set_password("changed-organizer-password")
        organizer.is_active = False
        organizer.save(update_fields=["password", "is_active"])
        bootstrap()
        organizer.refresh_from_db()
        self.assertFalse(organizer.is_active)
        self.assertTrue(organizer.check_password("changed-organizer-password"))

    def test_reset_links_created_with_demo_access_are_retired(self):
        admin = User.objects.get(email="admin@verdict.local")
        legitimate = User.objects.create_user("real@production.test", "legitimate-user-password")
        token = PasswordResetToken.objects.create(
            user=legitimate, created_by=admin, token_hash="a" * 64,
            expires_at=timezone.now() + timedelta(days=1),
        )
        bootstrap()
        token.refresh_from_db()
        self.assertIsNotNone(token.used_at)
        legitimate.refresh_from_db()
        self.assertTrue(legitimate.check_password("legitimate-user-password"))

    def test_invalid_provisioning_credentials_fail_without_partial_writes(self):
        original_users = User.objects.count()
        for email, password in (("invalid", "secure-passphrase"), ("operator@production.test", "short")):
            with self.subTest(email=email), patch.dict(os.environ, {
                "ADMIN_EMAIL": email, "ADMIN_PASSWORD": password,
            }):
                with self.assertRaises(ImproperlyConfigured):
                    bootstrap()
                self.assertEqual(User.objects.count(), original_users)
                self.assertFalse(AuditEvent.objects.filter(action="bootstrap.admin_provisioned").exists())

    def test_existing_account_cannot_be_taken_over_using_admin_email(self):
        operator = User.objects.create_user("operator@production.test", "existing-owner-password")
        with self.assertRaises(ImproperlyConfigured):
            bootstrap()
        operator.refresh_from_db()
        self.assertFalse(operator.is_admin)
        self.assertTrue(operator.check_password("existing-owner-password"))
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "existing-owner-password"}):
            bootstrap()
        operator.refresh_from_db()
        self.assertTrue(operator.is_admin)
        self.assertTrue(operator.check_password("existing-owner-password"))

    def test_production_banner_does_not_advertise_demo_logins(self):
        text = banner(bootstrap())
        self.assertIn("production mode", text)
        self.assertNotIn(DEMO_PASSWORD, text)
        self.assertNotIn("vd_demo_", text)

    def test_demo_shortcut_session_expires_even_when_admin_password_was_changed(self):
        admin = User.objects.get(email="admin@verdict.local")
        admin.set_password("changed-admin-password")
        admin.save(update_fields=["password"])
        with override_settings(DEMO_MODE=True):
            response = self.client.post("/api/v1/auth/demo-login", {"role": "admin"},
                                        content_type="application/json")
        self.assertEqual(response.status_code, 200)
        bootstrap()
        self.assertEqual(self.client.get("/api/v1/admin/users").status_code, 401)
        admin.refresh_from_db()
        self.assertTrue(admin.check_password("changed-admin-password"))
        self.assertEqual(self.client.post("/api/v1/auth/login", {
            "email": admin.email, "password": "changed-admin-password",
        }, content_type="application/json").status_code, 200)
        bootstrap()
        self.assertEqual(self.client.get("/api/v1/admin/users").status_code, 200)

    def test_legacy_unmarked_demo_session_requires_one_new_login(self):
        admin = User.objects.get(email="admin@verdict.local")
        admin.set_password("changed-admin-password")
        admin.save(update_fields=["password"])
        self.client.force_login(admin)
        bootstrap()
        self.assertEqual(self.client.get("/api/v1/admin/users").status_code, 401)
        admin.refresh_from_db()
        self.assertTrue(admin.check_password("changed-admin-password"))


@override_settings(DEMO_MODE=False)
class FreshProductionBootstrapTests(TestCase):
    def test_missing_credentials_stop_fresh_boot_without_seed_changes(self):
        with patch.dict(os.environ, {"ADMIN_EMAIL": "", "ADMIN_PASSWORD": ""}):
            with self.assertRaises(ImproperlyConfigured):
                bootstrap()
        self.assertFalse(User.objects.exists())
        self.assertFalse(Event.objects.exists())


class ProjectLockRegressionTests(TestCase):
    def setUp(self):
        self.stamp = timezone.now()
        self.owner = User.objects.create_user("owner@lock.test", "secret-password")
        self.other = User.objects.create_user("other@lock.test", "secret-password")
        self.event = Event.objects.create(
            slug="project-lock", name="Project locks", created_by=self.owner,
            submissions_close_at=self.stamp + timedelta(days=1),
        )
        self.team = Team.objects.create(event=self.event, name="Draft team")
        TeamMember.objects.create(event=self.event, team=self.team, user=self.owner, is_owner=True)
        self.project = Project.objects.create(event=self.event, team=self.team, title="Trackless")

    def test_trackless_draft_can_be_edited_and_withdrawn(self):
        edited = project_services.update_project(self.owner, self.event, self.project, {"summary": "Updated"})
        self.assertIsNone(edited.track_id)
        self.assertEqual(edited.summary, "Updated")
        withdrawn = project_services.withdraw_project(self.owner, edited)
        self.assertEqual(withdrawn.status, ProjectStatus.WITHDRAWN)

    def test_tracked_draft_can_submit_and_outsider_cannot_edit(self):
        track = Track.objects.create(event=self.event, name="Open")
        edited = project_services.update_project(self.owner, self.event, self.project, {
            "track": track.public_id, "summary": "Ready", "description": "Working project",
            "repo_url": "https://example.org/repository",
        })
        submitted = project_services.submit_project(self.owner, edited)
        self.assertEqual(submitted.status, ProjectStatus.SUBMITTED)
        with self.assertRaises(ApiError) as error:
            project_services.update_project(self.other, self.event, submitted, {"title": "Intrusion"})
        self.assertEqual(error.exception.status_code, 403)

    def test_stale_event_cannot_edit_after_database_window_closed(self):
        Event.objects.filter(pk=self.event.pk).update(submissions_close_at=self.stamp)
        with patch("projects.services.now", return_value=self.stamp), patch("events.policy.now", return_value=self.stamp):
            with self.assertRaises(ApiError) as error:
                project_services.update_project(self.owner, self.event, self.project, {"title": "Too late"})
        self.assertEqual(error.exception.code, "window_closed")
        self.project.refresh_from_db()
        self.assertEqual(self.project.title, "Trackless")

    def test_stale_event_cannot_create_after_database_window_closed(self):
        Event.objects.filter(pk=self.event.pk).update(submissions_close_at=self.stamp)
        with patch("projects.services.now", return_value=self.stamp), patch("events.policy.now", return_value=self.stamp):
            with self.assertRaises(ApiError) as error:
                project_services.create_project(self.owner, self.event, {"title": "Too late"})
        self.assertEqual(error.exception.code, "window_closed")

    def test_postgresql_sql_does_not_lock_nullable_track_join(self):
        pg = DatabaseWrapper({"ENGINE": "django.db.backends.postgresql", "NAME": "sql_only"})
        queries = []
        real_get = QuerySet.get

        def capture(queryset, *args, **kwargs):
            if queryset.model is Project:
                queries.append(queryset.filter(*args, **kwargs).query)
            return real_get(queryset, *args, **kwargs)

        with patch.object(QuerySet, "get", autospec=True, side_effect=capture):
            project_services._lock_project(self.project)
        with patch.object(pg, "get_autocommit", return_value=False):
            sql, _params = queries[-1].get_compiler(connection=pg).as_sql()
        self.assertIn("LEFT OUTER JOIN", sql)
        self.assertIn("FOR UPDATE OF", sql)
        lock_clause = sql.split("FOR UPDATE OF", 1)[1]
        self.assertIn('"projects_project"', lock_clause)
        self.assertNotIn('"events_track"', lock_clause)


class PublicationGuardRegressionTests(TestCase):
    def setUp(self):
        self.stamp = timezone.now()
        self.organizer = User.objects.create_user("org@publish.test", "secret-password")
        self.outsider = User.objects.create_user("outsider@publish.test", "secret-password")
        self.event = Event.objects.create(
            slug="publish-guard", name="Publication guard", created_by=self.organizer,
            submissions_close_at=self.stamp - timedelta(days=2),
            judging_open_at=self.stamp - timedelta(days=1), judging_close_at=self.stamp,
            shrinkage_lambda=2,
        )
        self.role = EventRole.objects.create(event=self.event, user=self.organizer, role=Role.ORGANIZER)
        rubric = Rubric.objects.create(event=self.event)
        Criterion.objects.create(rubric=rubric, key="quality", name="Quality", weight=1)

    def test_future_judging_close_cannot_publish_through_api(self):
        Event.objects.filter(pk=self.event.pk).update(judging_close_at=self.stamp + timedelta(hours=2))
        self.client.force_login(self.organizer)
        response = self.client.post(f"/api/v1/events/{self.event.slug}/results/publish", {}, content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "judging_open")
        self.assertFalse(ResultPublication.objects.exists())
        self.assertEqual(Client().get(f"/api/v1/events/{self.event.slug}/results").status_code, 404)

    def test_null_and_future_judging_close_are_denied(self):
        for close in (None, self.stamp + timedelta(microseconds=1)):
            with self.subTest(close=close):
                Event.objects.filter(pk=self.event.pk).update(judging_close_at=close)
                with patch("results.services.now", return_value=self.stamp):
                    with self.assertRaises(ApiError) as error:
                        result_services.publish(self.organizer, self.event)
                self.assertEqual(error.exception.code, "judging_open")

    def test_exact_judging_close_and_past_close_are_allowed(self):
        for close in (self.stamp, self.stamp - timedelta(microseconds=1)):
            with self.subTest(close=close):
                Event.objects.filter(pk=self.event.pk).update(judging_close_at=close)
                with patch("results.services.now", return_value=self.stamp):
                    publication = result_services.publish(self.organizer, self.event, note="Boundary check")
                self.assertEqual(publication.published_by, self.organizer)

    def test_service_requires_authenticated_organizer(self):
        for actor, status in ((None, 401), (AnonymousUser(), 401), (self.outsider, 403)):
            with self.subTest(actor=actor):
                with self.assertRaises(ApiError) as error:
                    result_services.publish(actor, self.event)
                self.assertEqual(error.exception.status_code, status)
        self.assertFalse(ResultPublication.objects.exists())

    def test_revoked_organizer_role_is_rechecked_inside_service(self):
        EventRole.objects.filter(pk=self.role.pk).update(role=Role.PARTICIPANT)
        with self.assertRaises(ApiError) as error:
            result_services.publish(self.organizer, self.event)
        self.assertEqual(error.exception.status_code, 403)

    def test_platform_admin_can_publish(self):
        admin = User.objects.create_user("admin@publish.test", "secret-password", is_admin=True)
        self.assertEqual(result_services.publish(admin, self.event).published_by, admin)

    def test_configured_voting_requires_a_close_and_rejects_future_close(self):
        VotingConfig.objects.create(event=self.event)
        for close in (None, self.stamp + timedelta(microseconds=1)):
            with self.subTest(close=close):
                Event.objects.filter(pk=self.event.pk).update(voting_close_at=close)
                with patch("results.services.now", return_value=self.stamp):
                    with self.assertRaises(ApiError) as error:
                        result_services.publish(self.organizer, self.event)
                self.assertEqual(error.exception.code, "voting_open")

    def test_exact_voting_close_is_allowed(self):
        VotingConfig.objects.create(event=self.event)
        Event.objects.filter(pk=self.event.pk).update(voting_close_at=self.stamp)
        with patch("results.services.now", return_value=self.stamp):
            self.assertIsNotNone(result_services.publish(self.organizer, self.event).pk)


class ResultOrderRegressionTests(SimpleTestCase):
    def test_shared_first_place_precedes_lower_ranks_and_unranked_rows(self):
        rows = [("4", "Fourth"), ("=1", "Zulu"), (None, "Absent"),
                ("=1", "Alpha"), ("3", "Third"), ("unranked", "Unranked")]
        ordered = sorted(rows, key=lambda row: result_services._rank_order(*row))
        self.assertEqual([title for _rank, title in ordered],
                         ["Alpha", "Zulu", "Third", "Fourth", "Absent", "Unranked"])
