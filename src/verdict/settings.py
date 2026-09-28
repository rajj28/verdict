"""Django settings for VERDICT.

Everything that differs between a laptop, the test runner and the container is
read from the environment here so the rest of the code never touches os.environ.
"""
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent  # src/
REPO_DIR = BASE_DIR.parent

DATA_DIR = Path(os.environ.get("DATA_DIR") or (REPO_DIR / ".data")).resolve()
FIXTURES_PATH = Path(os.environ.get("FIXTURES_PATH") or (REPO_DIR / "fixtures.json")).resolve()
MEDIA_ROOT = DATA_DIR / "media"
# Overridable so the image can collectstatic into a baked directory: /data is a
# volume at runtime and would otherwise shadow whatever the build produced.
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT") or (DATA_DIR / "static")).resolve()

DEBUG = os.environ.get("DEBUG", "0") == "1"
DEMO_MODE = os.environ.get("DEMO_MODE", "0") == "1"
WEBHOOKS_ALLOW_PRIVATE = os.environ.get("WEBHOOKS_ALLOW_PRIVATE") == "1"

# Offline messages stay in a private database outbox. Demo mode never uses SMTP.
# Tests can still override EMAIL_BACKEND with Django's in-memory backend.
EMAIL_HOST = os.environ.get("EMAIL_HOST", "").strip()
EMAIL_BACKEND = (
    "core.mail.OutboxBackend" if DEMO_MODE or not EMAIL_HOST
    else "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "0") == "1"
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "0" if EMAIL_USE_SSL else "1") == "1"
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "no-reply@verdict.local")


def _secret_key() -> str:
    """Env wins; otherwise a generated key persisted in DATA_DIR/secret_key.

    Persisting it keeps sessions and signed data valid across container restarts.
    """
    from_env = os.environ.get("SECRET_KEY")
    if from_env:
        return from_env
    key_path = DATA_DIR / "secret_key"
    try:
        existing = key_path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing
    generated = secrets.token_urlsafe(64)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        key_path.write_text(generated, encoding="utf-8")
        key_path.chmod(0o600)
    except OSError:
        # Read-only filesystem (e.g. CI checkouts): fall back to an ephemeral key.
        pass
    return generated


SECRET_KEY = _secret_key()

_DEFAULT_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "web", "[::1]"]
_extra_hosts = [h.strip() for h in (os.environ.get("ALLOWED_HOSTS") or "").split(",") if h.strip()]
if DEMO_MODE:
    ALLOWED_HOSTS = ["*"]
elif _extra_hosts:
    ALLOWED_HOSTS = _extra_hosts
else:
    ALLOWED_HOSTS = list(_DEFAULT_HOSTS)

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "core",
    "accounts",
    "events",
    "teams",
    "projects",
    "judging",
    "results",
    "community",
    "audit",
    "interop",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "verdict.urls"
WSGI_APPLICATION = "verdict.wsgi.application"
ASGI_APPLICATION = "verdict.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.static",
                "core.context_processors.portal",
            ],
        },
    },
]


def _database_from_url(url: str) -> dict:
    """Parse postgres://user:pass@host:port/name (and query params like sslmode).

    VERDICT only supports PostgreSQL; any other scheme (including sqlite://)
    is rejected here so misconfiguration fails at startup, not at first query.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured(
            "DATABASE_URL must use the postgres:// or postgresql:// scheme "
            f"(got {parsed.scheme or '<empty>'!r}). VERDICT only supports "
            "PostgreSQL. See .env.example and AGENTS.md for how to point this "
            "at your local Postgres (Docker Compose or an isolated container)."
        )
    name = (parsed.path or "/").lstrip("/")
    if not name:
        raise ImproperlyConfigured("DATABASE_URL is missing a database name.")
    options = dict(parse_qsl(parsed.query)) if parsed.query else {}
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": 60,
        "OPTIONS": options,
    }


_database_url = os.environ.get("DATABASE_URL") or ""
if not _database_url:
    raise ImproperlyConfigured(
        "DATABASE_URL is required and must be a PostgreSQL URL "
        "(postgres://user:pass@host:port/name). VERDICT does not support "
        "SQLite anywhere, including tests. Point it at your local Postgres: "
        "the Docker Compose 'db' service (via 'docker compose exec web ...' "
        "if it has no published host port) or an isolated PostgreSQL "
        "container/test database. See .env.example and AGENTS.md."
    )
DATABASES = {"default": _database_from_url(_database_url)}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
# Not a manifest: media and static are served without a collectstatic manifest.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

MEDIA_URL = "/media/"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7  # 7 days

CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = (
    ["http://localhost:8000", "http://localhost:8080", "http://127.0.0.1:8000", "http://127.0.0.1:8080"]
    + [f"{scheme}://{host}" for host in _extra_hosts for scheme in ("http", "https")]
)

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

LOGIN_URL = "/login"
LOGIN_REDIRECT_URL = "/"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "accounts.auth.BearerTokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "core.errors.api_exception_handler",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        # Global budgets from BUILD-SEC section 6; ScopedRateThrottle carries the
        # per-endpoint budgets (login) set with throttle_scope on the view.
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "120/min",
        "user": "1200/min",
        "login": "10/15min",
    },
    "DEFAULT_PAGINATION_CLASS": "core.pagination.VerdictPagination",
    "PAGE_SIZE": 50,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "VERDICT API",
    "DESCRIPTION": "Hackathon submission and judging portal. All writes go through this API.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    # Everything is served from the vendored sidecar: no CDN, no network at runtime.
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
}

DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024  # rule 9: images are capped at 5 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

if len(sys.argv) > 1 and sys.argv[1] == "test":
    # PBKDF2 hashing 120+ fixture users would dominate the suite runtime.
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"verdict": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}
