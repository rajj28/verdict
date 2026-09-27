"""Rate limits for the credential endpoints (BUILD-SEC section 6).

The budget is 10 attempts per 15 minutes per IP *and* email address, so one
shared office NAT cannot lock out a whole hackathon and one attacker cannot walk
a list of addresses from a single host.
"""
from django.core.exceptions import ImproperlyConfigured
from rest_framework.throttling import ScopedRateThrottle


def client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or "unknown"


def submitted_email(request) -> str:
    """The email in the request body, without letting a bad body raise here."""
    try:
        data = getattr(request, "data", None)
    except Exception:  # a malformed body is answered by the view, not the throttle
        return ""
    if isinstance(data, dict):
        return str(data.get("email") or "").strip().lower()[:120]
    return str(request.POST.get("email") or "").strip().lower()[:120]


class LoginThrottle(ScopedRateThrottle):
    """ScopedRateThrottle keyed on the pair (ip, email) instead of either alone.

    The view declares ``throttle_scope = "login"``; ScopedRateThrottle reads the
    scope from the view, which is what makes this class run at all, and this
    override is what makes the budget per address instead of per host.
    """

    #: Longest suffix first, so "15min" is minutes and not minutes-of-nothing.
    PERIODS = (("seconds", 1), ("second", 1), ("minutes", 60), ("minute", 60), ("min", 60),
               ("hours", 3600), ("hour", 3600), ("days", 86400), ("day", 86400),
               ("s", 1), ("m", 60), ("h", 3600), ("d", 86400))

    def parse_rate(self, rate):
        """Read "10/15min".

        DRF's own parser only looks at the first letter of the period, so the
        "10/15min" the spec asks for would raise a KeyError instead of limiting
        anything. This keeps the configured string and honours the full window.
        """
        if not rate:
            return (None, None)
        count, _separator, period = rate.partition("/")
        for suffix, seconds in self.PERIODS:
            if period.endswith(suffix):
                return int(count), int(period[: -len(suffix)] or 1) * seconds
        raise ImproperlyConfigured(f"Unsupported throttle period in {rate!r}.")

    def get_cache_key(self, request, view):
        if getattr(request.user, "is_authenticated", False):
            return None  # an authenticated caller is not brute forcing a password
        ident = f"{client_ip(request)}|{submitted_email(request)}"
        return self.cache_format % {"scope": self.scope, "ident": ident}
