"""DRF authentication: bearer token first, then session.

Order matters (BUILD-SEC section 16): with a bearer class that declares an
authenticate_header, DRF answers anonymous API calls with a real 401 instead of
falling back to a 403.
"""
from rest_framework import authentication, exceptions

from accounts.tokens import authenticate_token


class BearerTokenAuthentication(authentication.BaseAuthentication):
    """Authorization: Bearer vd_<40 url-safe chars>."""

    keyword = "Bearer"

    def authenticate_header(self, request) -> str:
        return 'Bearer realm="api"'

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode("latin-1")
        if not header:
            return None
        parts = header.split()
        if parts[0].lower() != self.keyword.lower():
            return None
        if len(parts) != 2:
            raise exceptions.AuthenticationFailed("Malformed Authorization header.")
        user = authenticate_token(parts[1])
        if user is None:
            raise exceptions.AuthenticationFailed("Invalid or revoked token.")
        return (user, None)
