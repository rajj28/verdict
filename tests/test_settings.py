"""Backend-configuration tests: DATABASE_URL parsing is PostgreSQL-only.

These reload ``verdict.settings`` under a patched ``DATABASE_URL`` to check
the parsing/validation logic; they do not open a database connection. This
is distinct from ``tests.test_engine``, which is pure stdlib and needs no
Django startup at all (run with ``python -m unittest tests.test_engine``).
"""
import importlib
import os
from unittest import TestCase

from django.core.exceptions import ImproperlyConfigured

from verdict import settings as verdict_settings


class DatabaseUrlTests(TestCase):
    """DATABASE_URL must be a valid postgres(ql):// URL; nothing else is accepted."""

    def setUp(self):
        self._original_url = os.environ.get("DATABASE_URL")

    def tearDown(self):
        if self._original_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._original_url
        importlib.reload(verdict_settings)

    def _reload_with(self, url):
        if url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url
        return importlib.reload(verdict_settings)

    def test_missing_database_url_fails_fast(self):
        with self.assertRaises(ImproperlyConfigured):
            self._reload_with(None)

    def test_empty_database_url_fails_fast(self):
        with self.assertRaises(ImproperlyConfigured):
            self._reload_with("")

    def test_sqlite_scheme_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            self._reload_with("sqlite:///./dev.sqlite3")

    def test_unsupported_scheme_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            self._reload_with("mysql://user:pass@host:3306/name")

    def test_missing_database_name_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            self._reload_with("postgres://verdict:secret@localhost:5432/")

    def test_postgres_scheme_accepted(self):
        module = self._reload_with("postgres://verdict:secret@localhost:5432/verdict")
        db = module.DATABASES["default"]
        self.assertEqual(db["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(db["NAME"], "verdict")
        self.assertEqual(db["HOST"], "localhost")
        self.assertEqual(db["PORT"], "5432")

    def test_postgresql_scheme_accepted(self):
        module = self._reload_with("postgresql://verdict:secret@localhost:5432/verdict")
        self.assertEqual(module.DATABASES["default"]["ENGINE"], "django.db.backends.postgresql")

    def test_percent_encoded_credentials_and_options(self):
        module = self._reload_with(
            "postgres://us%40er:p%40ss%3Aword@localhost:5432/verdict?sslmode=require"
        )
        db = module.DATABASES["default"]
        self.assertEqual(db["USER"], "us@er")
        self.assertEqual(db["PASSWORD"], "p@ss:word")
        self.assertEqual(db["OPTIONS"], {"sslmode": "require"})
