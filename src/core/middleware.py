"""Response security headers, set in middleware so no view can forget them."""

CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

# The embed route is designed to be framed, so it relaxes frame-ancestors only.
EMBED_CSP = CSP.replace("frame-ancestors 'none'", "frame-ancestors *")


class SecurityHeadersMiddleware:
    """Adds CSP and the hardening headers BUILD-SEC section 13 requires."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path
        if path.startswith("/embed/"):
            response["Content-Security-Policy"] = EMBED_CSP
            response["X-Frame-Options"] = "SAMEORIGIN"
        else:
            response["Content-Security-Policy"] = CSP
            response["X-Frame-Options"] = "DENY"
        response["X-Content-Type-Options"] = "nosniff"
        response["Referrer-Policy"] = "same-origin"
        return response
