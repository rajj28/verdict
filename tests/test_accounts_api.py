"""accounts: session login, registration, demo sign-in, tokens, admin flags, pages.

Covers BUILD-SPEC sections 6, 8, 12 and the hardening items in section 16 (generic
login error, validated next, CSRF on the anonymous endpoints, offline password
reset). Every rule has an allowed and a denied case.
"""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import PasswordResetToken
from accounts.tokens import authenticate_token, issue_token
from audit.models import AuditEvent
from core.bootstrap import DEMO_PASSWORD, bootstrap

User = get_user_model()

LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
REGISTER = "/api/v1/auth/register"
DEMO_LOGIN = "/api/v1/auth/demo-login"
ME = "/api/v1/me"
TOKENS = "/api/v1/me/tokens"
ADMIN_USERS = "/api/v1/admin/users"
RESET_PASSWORD = "/api/v1/auth/reset-password"


def post_json(client, path, payload, token=None):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


def patch_json(client, path, payload):
    return client.patch(path, data=json.dumps(payload), content_type="application/json")


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user("rosa@example.org", "correct-horse", display_name="Rosa")

    def test_login_starts_a_session(self):
        response = post_json(self.client, LOGIN, {"email": "rosa@example.org",
                                                  "password": "correct-horse"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["user"]["email"], "rosa@example.org")
        self.assertEqual(body["user"]["display_name"], "Rosa")
        self.assertEqual(body["redirect_to"], "/me")
        self.assertNotIn("password", json.dumps(body))
        self.assertEqual(self.client.get(ME).status_code, 200)

    def test_email_is_case_insensitive(self):
        response = post_json(self.client, LOGIN, {"email": "ROSA@Example.ORG",
                                                  "password": "correct-horse"})
        self.assertEqual(response.status_code, 200)

    def test_a_wrong_password_is_401_with_a_generic_message(self):
        response = post_json(self.client, LOGIN, {"email": "rosa@example.org", "password": "nope"})
        self.assertEqual(response.status_code, 401)
        error = response.json()["error"]
        self.assertEqual(error["code"], "invalid_credentials")
        self.assertEqual(error["message"], "Email or password is incorrect.")

    def test_an_unknown_email_gives_the_same_answer_as_a_wrong_password(self):
        unknown = post_json(self.client, LOGIN, {"email": "nobody@example.org", "password": "nope"})
        wrong = post_json(self.client, LOGIN, {"email": "rosa@example.org", "password": "nope"})
        self.assertEqual(unknown.status_code, wrong.status_code)
        self.assertEqual(unknown.json(), wrong.json())

    def test_a_deactivated_account_cannot_log_in(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        response = post_json(self.client, LOGIN, {"email": "rosa@example.org",
                                                  "password": "correct-horse"})
        self.assertEqual(response.status_code, 401)

    def test_missing_fields_are_400(self):
        response = post_json(self.client, LOGIN, {})
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json()["error"]["fields"])

    def test_ten_failures_then_429(self):
        payload = {"email": "throttle@example.org", "password": "wrong"}
        for _ in range(10):
            self.assertEqual(post_json(self.client, LOGIN, payload).status_code, 401)
        blocked = post_json(self.client, LOGIN, payload)
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["error"]["code"], "throttled")

    def test_the_budget_is_per_email_so_one_address_cannot_lock_out_another(self):
        for _ in range(10):
            post_json(self.client, LOGIN, {"email": "one@example.org", "password": "wrong"})
        blocked = post_json(self.client, LOGIN, {"email": "one@example.org", "password": "wrong"})
        other = post_json(self.client, LOGIN, {"email": "two@example.org", "password": "wrong"})
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(other.status_code, 401)

    def test_next_is_validated_against_this_host(self):
        response = post_json(self.client, LOGIN, {"email": "rosa@example.org",
                                                  "password": "correct-horse",
                                                  "next": "https://evil.example/steal"})
        self.assertEqual(response.json()["redirect_to"], "/me")
        internal = post_json(self.client, LOGIN, {"email": "rosa@example.org",
                                                  "password": "correct-horse",
                                                  "next": "/me/tokens"})
        self.assertEqual(internal.json()["redirect_to"], "/me/tokens")

    def test_logout_ends_the_session(self):
        post_json(self.client, LOGIN, {"email": "rosa@example.org", "password": "correct-horse"})
        self.assertEqual(self.client.get(ME).status_code, 200)
        self.assertEqual(post_json(self.client, LOGOUT, {}).status_code, 200)
        self.assertEqual(self.client.get(ME).status_code, 401)

    def test_login_without_csrf_is_403(self):
        strict = Client(enforce_csrf_checks=True)
        strict.get("/login")  # base.html guarantees the cookie exists
        self.assertIn("csrftoken", strict.cookies)
        blocked = post_json(strict, LOGIN, {"email": "rosa@example.org", "password": "correct-horse"})
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["error"]["code"], "csrf_failed")
        allowed = strict.post(
            LOGIN,
            data=json.dumps({"email": "rosa@example.org", "password": "correct-horse"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=strict.cookies["csrftoken"].value,
        )
        self.assertEqual(allowed.status_code, 200)


class RegisterTests(TestCase):
    def test_registration_creates_the_account_and_signs_in(self):
        response = post_json(self.client, REGISTER, {
            "email": "New.Person@Example.org",
            "password": "a-long-enough-password",
            "display_name": "New Person",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["user"]["email"], "new.person@example.org")
        user = User.objects.get(email="new.person@example.org")
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_admin)
        self.assertFalse(user.is_host)
        self.assertEqual(self.client.get(ME).status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="user.registered", actor=user).exists())

    def test_a_short_password_is_refused_with_a_field_error(self):
        response = post_json(self.client, REGISTER, {"email": "a@example.org", "password": "short"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.json()["error"]["fields"])
        self.assertFalse(User.objects.filter(email="a@example.org").exists())

    def test_a_malformed_email_is_refused(self):
        response = post_json(self.client, REGISTER, {"email": "not-an-email",
                                                     "password": "a-long-enough-password"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json()["error"]["fields"])

    def test_an_email_can_only_be_registered_once(self):
        User.objects.create_user("taken@example.org", "a-long-enough-password")
        response = post_json(self.client, REGISTER, {"email": "TAKEN@example.org",
                                                     "password": "a-long-enough-password"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "email_taken")
        self.assertEqual(User.objects.filter(email="taken@example.org").count(), 1)

    def test_registration_without_csrf_is_403(self):
        strict = Client(enforce_csrf_checks=True)
        strict.get("/register")
        response = post_json(strict, REGISTER, {"email": "a@example.org",
                                                "password": "a-long-enough-password"})
        self.assertEqual(response.status_code, 403)


@override_settings(DEMO_MODE=False)
class DemoLoginDisabledTests(TestCase):
    def test_demo_login_is_404_when_demo_mode_is_off(self):
        bootstrap()
        response = post_json(self.client, DEMO_LOGIN, {"role": "admin"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "demo_disabled")
        self.assertEqual(self.client.get(ME).status_code, 401)


@override_settings(DEMO_MODE=True)
class DemoLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        bootstrap()

    def test_every_seeded_role_can_sign_in_with_one_call(self):
        expected = {
            "admin": "admin@verdict.local",
            "organizer": "organizer@verdict.local",
            "judge_a": "diego.herrera@example.org",
            "judge_b": "jonas.vogel@example.org",
            "participant": "priya1@example.org",
        }
        for role, email in expected.items():
            with self.subTest(role=role):
                client = Client()
                response = post_json(client, DEMO_LOGIN, {"role": role})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["user"]["email"], email)
                self.assertEqual(response.json()["redirect_to"], "/me")
                self.assertEqual(client.get(ME).status_code, 200)

    def test_an_unknown_role_is_404(self):
        response = post_json(self.client, DEMO_LOGIN, {"role": "wizard"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "demo_account_missing")

    def test_the_seeded_password_works_too(self):
        response = post_json(self.client, LOGIN, {"email": "priya1@example.org",
                                                  "password": DEMO_PASSWORD})
        self.assertEqual(response.status_code, 200)

    def test_a_demo_sign_in_is_audited(self):
        post_json(self.client, DEMO_LOGIN, {"role": "judge_a"})
        self.assertTrue(
            AuditEvent.objects.filter(action="user.demo_login",
                                      actor__email="diego.herrera@example.org").exists()
        )


@override_settings(DEMO_MODE=True)
class MeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        bootstrap()

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_anonymous_is_401(self):
        self.assertEqual(self.client.get(ME).status_code, 401)

    def test_roles_are_reported_per_event(self):
        body = self.client.get(ME, HTTP_AUTHORIZATION="Bearer vd_demo_judge_a_91bc5d2e8f10").json()
        self.assertEqual(body["user"]["email"], "diego.herrera@example.org")
        roles = {role["event"]["slug"]: role for role in body["roles"]}
        self.assertIn("sample-hack-2026", roles)
        self.assertEqual(roles["sample-hack-2026"]["role"], "judge")
        self.assertEqual(roles["sample-hack-2026"]["public_id"], "jdg_24")
        self.assertNotIn("password", json.dumps(body))

    def test_the_password_is_never_in_the_payload(self):
        raw = self.client.get(ME, HTTP_AUTHORIZATION="Bearer vd_demo_admin_c0ffee5eed01").content
        self.assertNotIn(b"pbkdf2", raw.lower())
        self.assertNotIn(b"md5$", raw.lower())


class TokenApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user("rosa@example.org", "correct-horse", display_name="Rosa")
        self.other = User.objects.create_user("rui@example.org", "correct-horse")
        self.client.force_login(self.user)

    def test_the_plaintext_is_returned_once_and_never_again(self):
        created = post_json(self.client, TOKENS, {"name": "ci"})
        self.assertEqual(created.status_code, 201)
        body = created.json()
        plaintext = body["plaintext"]
        self.assertTrue(plaintext.startswith("vd_"))
        self.assertEqual(len(plaintext), 43)
        self.assertEqual(body["token"]["prefix"], plaintext[:12])

        listing = self.client.get(TOKENS).json()
        self.assertEqual(len(listing["tokens"]), 1)
        self.assertNotIn(plaintext, json.dumps(listing))
        self.assertNotIn("key_hash", json.dumps(listing))

    def test_a_fresh_token_authenticates_and_a_revoked_one_does_not(self):
        plaintext = post_json(self.client, TOKENS, {"name": "ci"}).json()["plaintext"]
        self.assertEqual(self.client.get(ME, HTTP_AUTHORIZATION=f"Bearer {plaintext}").status_code, 200)
        prefix = plaintext[:12]
        revoked = self.client.delete(f"{TOKENS}/{prefix}")
        self.assertEqual(revoked.status_code, 200)
        self.assertIsNotNone(revoked.json()["token"]["revoked_at"])
        after = self.client.get(ME, HTTP_AUTHORIZATION=f"Bearer {plaintext}")
        self.assertEqual(after.status_code, 401)
        self.assertEqual(after.json()["error"]["code"], "not_authenticated")
        self.assertIsNone(authenticate_token(plaintext))

    def test_revoking_twice_is_not_an_error(self):
        plaintext = post_json(self.client, TOKENS, {"name": "ci"}).json()["plaintext"]
        self.client.delete(f"{TOKENS}/{plaintext[:12]}")
        self.assertEqual(self.client.delete(f"{TOKENS}/{plaintext[:12]}").status_code, 200)

    def test_another_accounts_token_cannot_be_revoked(self):
        _token, plaintext = issue_token(self.other, "theirs")
        self.assertEqual(self.client.delete(f"{TOKENS}/{plaintext[:12]}").status_code, 404)

    def test_a_token_needs_a_name(self):
        response = post_json(self.client, TOKENS, {"name": "  "})
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.json()["error"]["fields"])

    def test_tokens_are_private_to_their_owner(self):
        post_json(self.client, TOKENS, {"name": "mine"})
        other_client = Client()
        other_client.force_login(self.other)
        self.assertEqual(other_client.get(TOKENS).json()["tokens"], [])

    def test_anonymous_token_access_is_401(self):
        self.assertEqual(Client().get(TOKENS).status_code, 401)
        self.assertEqual(Client().post(TOKENS, {}).status_code, 401)

    def test_creating_and_revoking_is_audited(self):
        plaintext = post_json(self.client, TOKENS, {"name": "ci"}).json()["plaintext"]
        self.client.delete(f"{TOKENS}/{plaintext[:12]}")
        actions = list(AuditEvent.objects.filter(actor=self.user)
                       .values_list("action", flat=True))
        self.assertIn("api_token.created", actions)
        self.assertIn("api_token.revoked", actions)


class AdminUserApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.admin = User.objects.create_user("boss@example.org", "correct-horse", is_admin=True)
        self.member = User.objects.create_user("rosa@example.org", "correct-horse")
        self.url = f"{ADMIN_USERS}/{self.member.public_id}"

    def test_a_non_admin_cannot_list_or_patch(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(ADMIN_USERS).status_code, 403)
        self.assertEqual(self.client.get(ADMIN_USERS).json()["error"]["code"], "forbidden")
        self.assertEqual(patch_json(self.client, self.url, {"is_host": True}).status_code, 403)
        self.assertFalse(User.objects.get(pk=self.member.pk).is_host)

    def test_anonymous_is_401(self):
        self.assertEqual(self.client.get(ADMIN_USERS).status_code, 401)
        self.assertEqual(patch_json(self.client, self.url, {"is_host": True}).status_code, 401)

    def test_an_admin_can_toggle_host_active_and_admin(self):
        self.client.force_login(self.admin)
        self.assertEqual(patch_json(self.client, self.url, {"is_host": True}).status_code, 200)
        self.assertTrue(User.objects.get(pk=self.member.pk).is_host)
        self.assertEqual(patch_json(self.client, self.url, {"is_active": False}).status_code, 200)
        self.assertFalse(User.objects.get(pk=self.member.pk).is_active)
        self.assertEqual(patch_json(self.client, self.url, {"is_admin": True}).status_code, 200)
        self.assertTrue(User.objects.get(pk=self.member.pk).is_admin)

    def test_the_list_never_exposes_a_hash_and_can_be_searched(self):
        self.client.force_login(self.admin)
        body = self.client.get(ADMIN_USERS).json()
        self.assertEqual(body["count"], 2)
        self.assertNotIn("password", json.dumps(body))
        found = self.client.get(ADMIN_USERS, {"q": "rosa"}).json()
        self.assertEqual(found["count"], 1)
        self.assertEqual(found["results"][0]["email"], "rosa@example.org")

    def test_an_admin_cannot_remove_their_own_admin_flag(self):
        self.client.force_login(self.admin)
        response = patch_json(self.client, f"{ADMIN_USERS}/{self.admin.public_id}",
                              {"is_admin": False})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "cannot_demote_self")
        self.assertTrue(User.objects.get(pk=self.admin.pk).is_admin)

    def test_an_admin_may_still_change_their_other_flags(self):
        self.client.force_login(self.admin)
        response = patch_json(self.client, f"{ADMIN_USERS}/{self.admin.public_id}",
                              {"is_host": False})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.get(pk=self.admin.pk).is_host)

    def test_an_unknown_user_is_404(self):
        self.client.force_login(self.admin)
        self.assertEqual(patch_json(self.client, f"{ADMIN_USERS}/usr_nope",
                                    {"is_host": True}).status_code, 404)

    def test_an_empty_patch_is_400(self):
        self.client.force_login(self.admin)
        self.assertEqual(patch_json(self.client, self.url, {}).status_code, 400)

    def test_every_flag_change_is_audited_with_old_and_new(self):
        self.client.force_login(self.admin)
        patch_json(self.client, self.url, {"is_host": True})
        event = AuditEvent.objects.filter(action="user.flags_changed").latest("created_at")
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.data["is_host"], {"from": False, "to": True})
        self.assertIn("rosa", event.summary.lower())


class PasswordResetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.admin = User.objects.create_user("boss@example.org", "correct-horse", is_admin=True)
        self.member = User.objects.create_user("rosa@example.org", "old-password-here")
        self.link_url = f"{ADMIN_USERS}/{self.member.public_id}/reset-link"

    def _token_from(self, client) -> str:
        response = client.post(self.link_url)
        self.assertEqual(response.status_code, 201)
        reset_url = response.json()["reset_url"]
        self.assertTrue(reset_url.startswith("/reset?token="))
        return reset_url.split("token=", 1)[1]

    def test_a_link_is_single_use(self):
        self.client.force_login(self.admin)
        token = self._token_from(self.client)
        stored = PasswordResetToken.objects.get()
        self.assertNotEqual(stored.token_hash, token)
        self.assertEqual(stored.expires_at > timezone.now(), True)

        first = post_json(self.client, RESET_PASSWORD, {"token": token, "password": "brand-new-password"})
        self.assertEqual(first.status_code, 200)
        self.assertTrue(User.objects.get(pk=self.member.pk).check_password("brand-new-password"))

        again = post_json(self.client, RESET_PASSWORD, {"token": token, "password": "another-password"})
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.json()["error"]["code"], "reset_token_used")
        self.assertTrue(User.objects.get(pk=self.member.pk).check_password("brand-new-password"))

    def test_the_new_password_logs_in(self):
        self.client.force_login(self.admin)
        token = self._token_from(self.client)
        post_json(self.client, RESET_PASSWORD, {"token": token, "password": "brand-new-password"})
        client = Client()
        self.assertEqual(
            post_json(client, LOGIN, {"email": "rosa@example.org",
                                      "password": "brand-new-password"}).status_code,
            200,
        )

    def test_an_expired_link_is_refused(self):
        self.client.force_login(self.admin)
        token = self._token_from(self.client)
        PasswordResetToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        response = post_json(self.client, RESET_PASSWORD, {"token": token, "password": "brand-new-password"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "reset_token_expired")

    def test_an_unknown_token_is_refused(self):
        response = post_json(self.client, RESET_PASSWORD, {"token": "nope", "password": "brand-new-password"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "reset_token_invalid")

    def test_a_weak_new_password_is_refused_and_the_link_stays_usable(self):
        self.client.force_login(self.admin)
        token = self._token_from(self.client)
        weak = post_json(self.client, RESET_PASSWORD, {"token": token, "password": "short"})
        self.assertEqual(weak.status_code, 400)
        self.assertIn("password", weak.json()["error"]["fields"])
        strong = post_json(self.client, RESET_PASSWORD, {"token": token, "password": "brand-new-password"})
        self.assertEqual(strong.status_code, 200)

    def test_a_new_link_retires_the_outstanding_one(self):
        self.client.force_login(self.admin)
        first = self._token_from(self.client)
        self._token_from(self.client)
        response = post_json(self.client, RESET_PASSWORD, {"token": first, "password": "brand-new-password"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "reset_token_used")

    def test_only_an_admin_can_generate_a_link(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(self.link_url).status_code, 403)
        self.assertEqual(Client().post(self.link_url).status_code, 401)

    def test_generating_and_using_a_link_is_audited(self):
        self.client.force_login(self.admin)
        token = self._token_from(self.client)
        post_json(self.client, RESET_PASSWORD, {"token": token, "password": "brand-new-password"})
        actions = list(AuditEvent.objects.values_list("action", flat=True))
        self.assertIn("user.reset_link_created", actions)
        self.assertIn("user.password_reset", actions)
        self.assertNotIn(token, json.dumps([event.data for event in AuditEvent.objects.all()],
                                           default=str))

    def test_changing_your_own_password_needs_the_current_one(self):
        self.client.force_login(self.member)
        wrong = post_json(self.client, "/api/v1/me/password",
                          {"current_password": "not-it", "password": "brand-new-password"})
        self.assertEqual(wrong.status_code, 400)
        self.assertIn("current_password", wrong.json()["error"]["fields"])
        ok = post_json(self.client, "/api/v1/me/password",
                       {"current_password": "old-password-here", "password": "brand-new-password"})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(User.objects.get(pk=self.member.pk).check_password("brand-new-password"))
        self.assertTrue(AuditEvent.objects.filter(action="user.password_changed").exists())


@override_settings(DEMO_MODE=True)
class PageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        bootstrap()

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_the_layout_is_the_real_one(self):
        body = self.client.get("/").content.decode()
        self.assertIn("VERDICT", body)
        self.assertIn("/static/vendor/bootstrap/bootstrap.min.css", body)
        self.assertIn("/static/css/app.css", body)
        self.assertIn("/static/js/api-forms.js", body)
        self.assertIn('name="csrf-token"', body)
        self.assertIn("Self-hosted", body)
        self.assertIn("/api/docs/", body)
        # The CSP forbids both of these, so their absence is a requirement.
        self.assertNotIn("<script>", body)
        self.assertNotIn(" onclick=", body)
        self.assertNotIn("http://cdn", body)
        self.assertNotIn("https://cdn", body)

    def test_the_demo_ribbon_appears_only_in_demo_mode(self):
        self.assertContains(self.client.get("/"), "Demo mode")
        with override_settings(DEMO_MODE=False):
            self.assertNotContains(self.client.get("/"), "Demo mode")

    def test_the_home_page_shows_real_numbers(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["stats"]["events"], 2)
        self.assertEqual(response.context["stats"]["projects"], 45)
        self.assertEqual(response.context["stats"]["judges"], 33)
        self.assertContains(response, 'class="ledger"')
        self.assertContains(response, '<span class="v">45</span>', html=False)
        self.assertContains(response, "Submissions open")

    def test_the_home_page_is_a_fixed_number_of_queries(self):
        with self.assertNumQueries(6):
            # 4 counts, 1 card list, 1 grouped project count.
            self.client.get("/")

    def test_the_login_page_offers_the_five_demo_roles(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        for role in ("admin", "organizer", "judge_a", "judge_b", "participant"):
            self.assertContains(response, f'&quot;{role}&quot;')
        self.assertContains(response, 'data-api-url="/api/v1/auth/login"')
        with override_settings(DEMO_MODE=False):
            self.assertNotContains(self.client.get("/login"), "judge_a")

    def test_the_register_page_renders(self):
        response = self.client.get("/register")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-api-url="/api/v1/auth/register"')
        for field in ("display_name", "email", "password"):
            self.assertContains(response, f'name="{field}"')

    def test_anonymous_dashboard_sends_you_to_the_login_page(self):
        response = self.client.get("/me")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/login?next=/me")

    def test_the_judge_dashboard_shows_the_role_and_the_queue(self):
        self.client.get("/login")
        post_json(self.client, DEMO_LOGIN, {"role": "judge_a"})
        response = self.client.get("/me")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "role-judge")
        self.assertContains(response, "Sample Hack 2026")
        self.assertContains(response, "jdg_24")
        self.assertEqual(response.context["cards"][0]["progress"]["submitted"],
                         response.context["cards"][0]["progress"]["assigned"]
                         - response.context["cards"][0]["progress"]["remaining"])

    def test_the_participant_dashboard_shows_the_team_and_the_project(self):
        self.client.get("/login")
        post_json(self.client, DEMO_LOGIN, {"role": "participant"})
        response = self.client.get("/me")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Glass Signal")
        self.assertContains(response, "NorthKiln")
        self.assertContains(response, "status-submitted")

    def test_the_dashboard_is_a_fixed_number_of_queries(self):
        self.client.get("/login")
        post_json(self.client, DEMO_LOGIN, {"role": "judge_a"})
        with self.assertNumQueries(6):
            # 1 session, 1 user, 1 roles + 1 judge tracks, 1 memberships,
            # 1 assignment progress. The nav reuses the roles the view loaded.
            self.client.get("/me")

    def test_the_participant_dashboard_is_a_fixed_number_of_queries(self):
        self.client.get("/login")
        post_json(self.client, DEMO_LOGIN, {"role": "participant"})
        with self.assertNumQueries(7):
            # One more than the judge: the team's project.
            self.client.get("/me")

    def test_the_tokens_page_renders_and_never_contains_a_secret(self):
        self.client.get("/login")
        post_json(self.client, DEMO_LOGIN, {"role": "organizer"})
        _token, plaintext = issue_token(User.objects.get(email="organizer@verdict.local"), "ci")
        body = self.client.get("/me/tokens").content.decode()
        self.assertNotIn(plaintext, body)
        self.assertIn(plaintext[:12], body)
        self.assertIn('data-api-url="/api/v1/me/tokens"', body)

    def test_the_admin_panel_is_admin_only(self):
        self.client.get("/login")
        post_json(self.client, DEMO_LOGIN, {"role": "organizer"})
        forbidden = self.client.get("/admin-panel/")
        self.assertEqual(forbidden.status_code, 403)
        self.assertIn("admin panel", forbidden.content.decode().lower())

        self.client.get("/login")
        post_json(self.client, DEMO_LOGIN, {"role": "admin"})
        response = self.client.get("/admin-panel/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "admin@verdict.local")
        self.assertContains(response, "reset-link")
        self.assertContains(response, "Sample Hack 2026")
        self.assertEqual(self.client.get("/admin-panel/").status_code, 200)

    def test_anonymous_admin_panel_sends_you_to_the_login_page(self):
        response = self.client.get("/admin-panel/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/login?next=/admin-panel/")

    def test_the_admin_panel_search_narrows_the_table(self):
        self.client.get("/login")
        post_json(self.client, DEMO_LOGIN, {"role": "admin"})
        response = self.client.get("/admin-panel/", {"q": "priya1"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "priya1@example.org")
        self.assertNotContains(response, "jonas.vogel@example.org")

    def test_the_reset_page_renders_with_and_without_a_token(self):
        self.assertContains(self.client.get("/reset?token=abc123"), "abc123")
        self.assertContains(self.client.get("/reset"), "No reset token")

    def test_a_missing_page_uses_the_branded_404(self):
        response = self.client.get("/no-such-page")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "VERDICT", status_code=404)
        self.assertContains(response, "404", status_code=404)
        self.assertContains(response, "/static/css/app.css", status_code=404)

    def test_the_500_page_stands_alone(self):
        from django.template.loader import render_to_string

        rendered = render_to_string("errors/500.html", {"PORTAL_NAME": "VERDICT"})
        self.assertIn("500", rendered)
        self.assertIn("/static/css/app.css", rendered)
        self.assertNotIn("{%", rendered)

    def test_the_gallery_still_renders_fixture_titles(self):
        response = self.client.get("/projects")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Glass Signal")
