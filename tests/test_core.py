"""core: public ids, the error envelope, CSV escaping and the clock."""
import csv
import io
import re
from datetime import datetime
from datetime import timezone as dt_timezone

from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied, Throttled
from rest_framework.views import APIView

from core.clock import now
from core.csvutil import safe_cell, write_csv
from core.errors import ApiError, api_exception_handler
from core.ids import new_public_id
from core.middleware import CSP

PUBLIC_ID_RE = re.compile(r"^[a-z]+_[a-z2-7]{10}$")


class PublicIdTests(SimpleTestCase):
    def test_format_is_prefix_underscore_ten_base32_chars(self):
        for prefix in ("prj", "usr", "tm", "rev", "pub"):
            value = new_public_id(prefix)
            self.assertRegex(value, PUBLIC_ID_RE)
            self.assertTrue(value.startswith(f"{prefix}_"))
            self.assertEqual(len(value), len(prefix) + 11)

    def test_alphabet_is_lowercase_base32_without_padding(self):
        # 1000 draws make a wrong alphabet (uppercase, 0/1/8/9) overwhelmingly unlikely to hide.
        body = "".join(new_public_id("x")[2:] for _ in range(1000))
        self.assertTrue(set(body) <= set("abcdefghijklmnopqrstuvwxyz234567"))

    def test_ids_do_not_repeat(self):
        ids = {new_public_id("prj") for _ in range(500)}
        self.assertEqual(len(ids), 500)


class ClockTests(SimpleTestCase):
    def test_now_is_timezone_aware_and_utc(self):
        moment = now()
        self.assertIsNotNone(moment.tzinfo)
        self.assertEqual(moment.utcoffset(), dt_timezone.utc.utcoffset(None))
        self.assertIsInstance(moment, datetime)


class ErrorEnvelopeTests(SimpleTestCase):
    def _response(self, exc):
        return api_exception_handler(exc, {"view": APIView(), "request": None, "args": [],
                                           "kwargs": {}})

    def test_api_error_keeps_its_code_message_and_fields(self):
        response = self._response(
            ApiError("window_closed", "Submissions closed at 2026-03-01T18:00:00Z.",
                     status_code=403, fields={"title": ["Required"]})
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "window_closed")
        self.assertIn("2026-03-01T18:00:00Z", response.data["error"]["message"])
        self.assertEqual(response.data["error"]["fields"], {"title": ["Required"]})

    def test_permission_denied_maps_to_403_forbidden(self):
        response = self._response(PermissionDenied("No."))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "forbidden")
        self.assertEqual(response.data["error"]["fields"], {})

    def test_throttled_maps_to_429_throttled(self):
        response = self._response(Throttled())
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["error"]["code"], "throttled")

    def test_api_error_defaults_to_400(self):
        self.assertEqual(ApiError("invalid_team", "Pick a team.").status_code, 400)
        self.assertEqual(ApiError("x", "y").fields, {})


class CsvTests(SimpleTestCase):
    def test_formula_prefixes_get_a_leading_quote(self):
        for raw in ["=1+1", "+1", "-1", "@SUM(A1)", "\ttab", "\rcr", "=cmd|' /C calc'!A0"]:
            escaped = safe_cell(raw)
            self.assertEqual(escaped, "'" + raw)
            # After the quote a spreadsheet reads the cell as text, not a formula.
            self.assertNotIn(escaped[0], "=+-@\t\r")

    def test_ordinary_cells_are_untouched(self):
        for raw in ["Glass Signal", "2026-03-01", "5", "priya1@example.org", "1-2", ""]:
            self.assertEqual(safe_cell(raw), raw)

    def test_none_and_bool_render_as_text(self):
        self.assertEqual(safe_cell(None), "")
        self.assertEqual(safe_cell(True), "true")
        self.assertEqual(safe_cell(False), "false")

    def test_write_csv_has_a_header_row_and_escapes_cells(self):
        document = write_csv(["title", "note"], [["=2+2", "safe"], ["NorthKiln", "-1"]])
        rows = list(csv.reader(io.StringIO(document)))
        self.assertEqual(rows[0], ["title", "note"])
        self.assertEqual(rows[1], ["'=2+2", "safe"])
        self.assertEqual(rows[2], ["NorthKiln", "'-1"])
        self.assertTrue(document.endswith("\n"))


class SecurityHeaderTests(SimpleTestCase):
    def test_csp_forbids_inline_script_and_frames(self):
        self.assertIn("script-src 'self'", CSP)
        self.assertIn("default-src 'self'", CSP)
        self.assertIn("frame-ancestors 'none'", CSP)
        self.assertNotIn("unsafe-eval", CSP)
