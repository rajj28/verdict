"""OpenAPI extensions for the parts of the API drf-spectacular cannot guess.

Kept in core so the schema is built from one place: without this, every
bearer-authenticated endpoint shows up in /api/docs/ with no security scheme
and the generated schema would misrepresent how a caller authenticates.
"""
from drf_spectacular.extensions import OpenApiAuthenticationExtension


class BearerTokenScheme(OpenApiAuthenticationExtension):
    """Document ``Authorization: Bearer vd_...`` as the API's security scheme."""

    target_class = "accounts.auth.BearerTokenAuthentication"
    name = "bearerAuth"
    priority = 1

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "description": "API token from POST /api/v1/me/tokens. No CSRF needed.",
        }
