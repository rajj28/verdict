"""One error envelope for the whole API: {"error": {code, message, fields}}."""
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

_CODE_BY_STATUS = {
    400: "invalid",
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    415: "unsupported_media_type",
    429: "throttled",
}


class ApiError(APIException):
    """Business-rule failure with a stable machine code.

    Services raise this instead of DRF exceptions so one rule yields the same
    envelope whether it is hit from a UI write or a direct API call.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "invalid"

    def __init__(self, code: str, message: str, status_code: int = 400, fields: dict | None = None):
        super().__init__(detail=message, code=code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.fields = fields or {}


def _envelope(code: str, message: str, fields: dict | None, status_code: int) -> Response:
    return Response(
        {"error": {"code": code, "message": message, "fields": fields or {}}},
        status=status_code,
    )


def _first_message(detail) -> str:
    if isinstance(detail, dict):
        for key, value in detail.items():
            return f"{key}: {_first_message(value)}"
        return "Invalid request."
    if isinstance(detail, list) and detail:
        return _first_message(detail[0])
    return str(detail)


def api_exception_handler(exc, context):
    """DRF exception handler: every error leaves as the same envelope shape."""
    if isinstance(exc, ApiError):
        return _envelope(exc.code, exc.message, exc.fields, exc.status_code)

    if isinstance(exc, ValidationError):
        return _envelope("invalid", "Please correct the highlighted fields.", dict(exc.detail), 400)

    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    detail = response.data
    if isinstance(detail, dict) and "detail" in detail:
        fields = {key: value for key, value in detail.items() if key != "detail"}
    elif isinstance(detail, dict):
        fields = detail
    else:
        fields = {}

    code = _CODE_BY_STATUS.get(response.status_code, "error")
    result = _envelope(code, _first_message(detail), fields, response.status_code)
    if response.status_code == 401:
        # Keep the challenge DRF computed from the first authenticator, otherwise
        # clients cannot tell "log in" from "forbidden".
        auth_header = getattr(exc, "auth_header", None)
        if auth_header:
            result["WWW-Authenticate"] = auth_header
    return result
