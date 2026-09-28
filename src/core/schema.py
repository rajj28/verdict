"""OpenAPI extensions for the parts of the API drf-spectacular cannot guess.

Kept in core so the schema is built from one place: without this, every
bearer-authenticated endpoint shows up in /api/docs/ with no security scheme
and the generated schema would misrepresent how a caller authenticates.

It also owns the documented error envelope. Every failure in VERDICT leaves
through ``core.errors.api_exception_handler`` as
``{"error": {"code", "message", "fields"}}``, so every operation in the
schema advertises that same shape for each status it can actually return.
"""
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import OpenApiResponse
from rest_framework import serializers

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


class ErrorBodySerializer(serializers.Serializer):
    """The ``error`` object every non-2xx response carries."""

    code = serializers.CharField(
        help_text="Stable machine code, e.g. 'deadline_closed' or 'not_authenticated'."
    )
    message = serializers.CharField(help_text="Human sentence, safe to show to the caller.")
    fields = serializers.DictField(
        help_text="Field name to message list when the failure is per-field; "
                 "empty otherwise.",
    )


class ErrorEnvelopeSerializer(serializers.Serializer):
    """The one and only error shape in this API."""

    error = ErrorBodySerializer()


#: Documented meaning of each status the envelope can carry.
ERROR_DESCRIPTIONS: dict[int, str] = {
    400: "Rejected: malformed body, a field that failed validation, or a rule that "
         "returns 400. See error.code.",
    401: "Not authenticated: no session cookie and no usable Authorization: Bearer token.",
    403: "Authenticated but not allowed. The rule that refused is named in error.code.",
    404: "No such object, or it exists but the caller may not know it exists.",
    405: "Method not allowed on this path.",
    409: "Conflict: the object moved on (a closed window, a duplicate, a stale edit).",
    410: "Gone: the invite or capability this addressed has been revoked or replaced.",
    415: "Unsupported media type; JSON is expected except on upload endpoints.",
    429: "Rate limited. Retry after the window named in the Retry-After header.",
}


def error_response(status_code: int, description: str | None = None) -> OpenApiResponse:
    """One documented error response, always the same envelope."""
    return OpenApiResponse(
        response=ErrorEnvelopeSerializer,
        description=description or ERROR_DESCRIPTIONS.get(status_code, "Error."),
    )


def error_responses(*status_codes: int) -> dict[int, OpenApiResponse]:
    """Shorthand for ``responses={code: error_response(code), ...}``."""
    return {code: error_response(code) for code in status_codes}
