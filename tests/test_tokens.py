"""accounts: bearer token issue, authentication, revocation and the DRF class."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from accounts.auth import BearerTokenAuthentication
from accounts.models import ApiToken, User
from accounts.tokens import (
    authenticate_token,
    generate_plaintext,
    issue_token,
    revoke_token,
)

DEMO_TOKEN = "vd_demo_organizer_7f2a91c4e0b3"


class _ProbeView(APIView):
    """Minimal authenticated endpoint so the authentication classes can be exercised."""

    authentication_classes = [BearerTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"email": request.user.email})


class TokenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rosa@example.org", "verdict-demo", display_name="Rosa")

    def test_issue_returns_plaintext_that_authenticates(self):
        token, plaintext = issue_token(self.user, "ci")
        self.assertTrue(plaintext.startswith("vd_"))
        self.assertEqual(len(plaintext), 43)
        self.assertEqual(authenticate_token(plaintext), self.user)
        self.assertEqual(token.prefix, plaintext[:12])
        self.assertFalse(token.is_demo)

    def test_only_the_hash_is_stored(self):
        token, plaintext = issue_token(self.user, "ci")
        self.assertNotEqual(token.key_hash, plaintext)
        self.assertEqual(len(token.key_hash), 64)
        self.assertEqual(ApiToken.objects.get(pk=token.pk).key_hash, token.key_hash)

    def test_generated_plaintext_shape(self):
        for _ in range(20):
            plaintext = generate_plaintext()
            self.assertTrue(plaintext.startswith("vd_"))
            self.assertEqual(len(plaintext), 43)

    def test_wrong_token_returns_none(self):
        issue_token(self.user, "ci")
        self.assertIsNone(authenticate_token("vd_" + "z" * 40))
        self.assertIsNone(authenticate_token(""))
        self.assertIsNone(authenticate_token(None))
        self.assertIsNone(authenticate_token("not-a-verdict-token"))

    def test_revoked_token_stops_authenticating(self):
        token, plaintext = issue_token(self.user, "ci")
        revoke_token(token)
        self.assertIsNotNone(token.revoked_at)
        self.assertIsNone(authenticate_token(plaintext))
        # Revoking twice is a no-op, not an error.
        first = token.revoked_at
        revoke_token(token)
        self.assertEqual(token.revoked_at, first)

    def test_inactive_user_cannot_authenticate(self):
        _token, plaintext = issue_token(self.user, "ci")
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertIsNone(authenticate_token(plaintext))

    def test_last_used_at_is_written_at_most_once_a_minute(self):
        _token, plaintext = issue_token(self.user, "ci")
        self.assertIsNone(ApiToken.objects.get(user=self.user).last_used_at)

        authenticate_token(plaintext)
        first = ApiToken.objects.get(user=self.user).last_used_at
        self.assertIsNotNone(first)

        with patch("accounts.tokens.now", return_value=first + timedelta(seconds=30)):
            authenticate_token(plaintext)
        self.assertEqual(ApiToken.objects.get(user=self.user).last_used_at, first)

        with patch("accounts.tokens.now", return_value=first + timedelta(minutes=2)):
            authenticate_token(plaintext)
        self.assertGreater(ApiToken.objects.get(user=self.user).last_used_at, first)

    def test_deterministic_plaintext_can_be_reissued(self):
        token, plaintext = issue_token(self.user, "demo organizer", DEMO_TOKEN, is_demo=True)
        self.assertEqual(plaintext, DEMO_TOKEN)
        self.assertTrue(token.is_demo)
        self.assertEqual(authenticate_token(DEMO_TOKEN), self.user)


class BearerAuthenticationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = User.objects.create_user("rosa@example.org", "verdict-demo")
        _token, self.plaintext = issue_token(self.user, "ci")

    def _get(self, header=None):
        headers = {"HTTP_AUTHORIZATION": header} if header else {}
        return _ProbeView.as_view()(self.factory.get("/api/v1/me", **headers))

    def test_valid_bearer_token_authenticates(self):
        response = self._get(f"Bearer {self.plaintext}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "rosa@example.org")

    def test_anonymous_call_is_401_not_403(self):
        response = self._get()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"]["code"], "not_authenticated")
        self.assertEqual(response["WWW-Authenticate"], 'Bearer realm="api"')

    def test_bad_token_is_401(self):
        response = self._get("Bearer vd_" + "x" * 40)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["error"]["code"], "not_authenticated")

    def test_malformed_header_is_rejected(self):
        self.assertEqual(self._get("Bearer").status_code, 401)
        self.assertEqual(self._get("Bearer a b").status_code, 401)

    def test_other_schemes_are_ignored(self):
        # Not our header: the view has no other authenticator, so the caller is anonymous.
        self.assertEqual(self._get("Basic cm9zYTpwdw==").status_code, 401)
