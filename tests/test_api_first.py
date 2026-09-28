"""API-first proof for packet R02B: schema, drift, UI parity, completeness, docs.

Why this file exists: the API-first claim ("every UI action maps to a
documented API operation") rots silently whenever a template or view changes.
These tests pin the generated OpenAPI schema, the committed
``docs/openapi.yaml``, every ``data-api-*`` control in the templates, the
``/api/v1/`` calls in ``src/static/js/``, and the prose in ``docs/API.md``
against each other, so any drift fails the suite instead of the demo.
"""
import re
from pathlib import Path

import yaml
from django.test import SimpleTestCase
from drf_spectacular.drainage import GENERATOR_STATS, reset_generator_stats
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema

REPO_DIR = Path(__file__).resolve().parent.parent
OPENAPI_PATH = REPO_DIR / "docs" / "openapi.yaml"
API_MD_PATH = REPO_DIR / "docs" / "API.md"
TEMPLATES_DIR = REPO_DIR / "src" / "templates"
STATIC_JS_DIR = REPO_DIR / "src" / "static" / "js"

REGENERATE_HINT = (
    "docs/openapi.yaml drifted from the code. Regenerate it with:\n"
    ".venv/Scripts/python.exe manage.py spectacular "
    "--file docs/openapi.yaml --validate --fail-on-warn"
)

ERROR_ENVELOPE_REF = "#/components/schemas/ErrorEnvelope"

# Template context variables that hold a whole API URL (the view builds them
# from the event slug; citations are the view that provides each one).
# Mixed values like "{{ assignments_api }}/{{ row.public_id }}" resolve the
# prefix first, then every remaining {{ ... }} becomes a path parameter.
CONTEXT_URLS = {
    # community/views.py
    "api_url::ballot": "/api/v1/events/{slug}/votes/ballot",
    "api_url::email_verify": "/api/v1/events/{slug}/votes/email/verify",
    "config_api_url": "/api/v1/events/{slug}/voting/config",
    # core/views.py, core/outbox_views.py
    "endpoint": "/api/v1/integrity/probe",
    "api_url::outbox": "/api/v1/events/{slug}/outbox",
    # judging/views.py
    "draft_url": "/api/v1/events/{slug}/judge/reviews/{prj_id}",
    "submit_url": "/api/v1/events/{slug}/judge/reviews/{prj_id}/submit",
    "api_url::command_center": "/api/v1/events/{slug}/command-center",
    "rebalance_url": "/api/v1/events/{slug}/assignments/rebalance",
    "progress_api": "/api/v1/events/{slug}/progress",
    # results/views.py
    "assignments_api": "/api/v1/events/{slug}/assignments",
    "auto_api": "/api/v1/events/{slug}/assignments/auto",
    "rubric_api": "/api/v1/events/{slug}/rubric",
    "judges_api": "/api/v1/events/{slug}/judges",
    "invites_api": "/api/v1/events/{slug}/judge-invites",
    "conflicts_api": "/api/v1/events/{slug}/conflicts",
    "close_judging_api": "/api/v1/events/{slug}/close-judging",
    "publish_api": "/api/v1/events/{slug}/results/publish",
    "consequences_api": "/api/v1/events/{slug}/results/consequences",
    "feedback_api": "/api/v1/events/{slug}/feedback-release",
    "verify_api": "/api/v1/events/{slug}/results/publications/{pub_id}/verify",
}

# Ambiguous context names resolved per template file.
CONTEXT_URLS_BY_FILE = {
    "community/ballot.html::api_url": CONTEXT_URLS["api_url::ballot"],
    "community/email_verify.html::api_url": CONTEXT_URLS["api_url::email_verify"],
    "core/outbox.html::api_url": CONTEXT_URLS["api_url::outbox"],
    "manage/command_center.html::api_url": CONTEXT_URLS["api_url::command_center"],
}

# In-flight tour console (packet F5-TOUR, uncommitted working tree): the views
# render but drf-spectacular cannot guess a serializer for the two plain
# APIViews without serializer_class, so generation emits exactly these errors.
# Owned by the tour worker; delete this allow-list once src/core/tour.py
# documents them. Anything else in the stats fails the test.
KNOWN_GENERATOR_ERROR_FRAGMENTS = (
    "Error [TourStartView]: unable to guess serializer",
    "Error [TourResetView]: unable to guess serializer",
)

# In-flight tour operations (same worker): rendered without a summary and
# without documented error responses yet. operationId and 2xx are still
# required; remove once src/core/tour.py annotates them.
TOUR_INFLIGHT_OPERATION_IDS = frozenset(
    {"tour_start", "tour_switch_role", "tour_reset", "tour_step"}
)

# Committed operations whose error responses do not reference the shared
# ErrorEnvelope component yet. Each entry is a (method, schema path) pair
# whose 4xx/5xx responses are description-only or a generic object.
# Owned by the respective area workers; shrink this set, never grow it.
ENVELOPE_EXCEPTIONS = frozenset(
    {
        ("GET", "/api/v1/admin/outbox"),
        ("GET", "/api/v1/events/{slug}/outbox"),
        ("GET", "/api/v1/events/{slug}/decision-room"),
        ("POST", "/api/v1/events/{slug}/votes/email"),
    }
)

# UI write actions that are genuinely not API calls. Empty by construction:
# every form and button posts through data-api-* (BUILD-SPEC section 2), and
# every dynamic fetch in src/static/js/ targets a data-* URL the templates
# provide. Keep entries here commented with a reason if one ever appears.
NON_API_ALLOW_LIST = frozenset()  # type: frozenset[tuple[str, str]]


def generate_schema():
    """Build the schema exactly as `manage.py spectacular` does."""
    reset_generator_stats()
    schema = SchemaGenerator().get_schema(request=None, public=True)
    warnings = sorted(GENERATOR_STATS._warn_cache)
    errors = sorted(GENERATOR_STATS._error_cache)
    return schema, warnings, errors


_cached = {}


def cached_generation():
    if "schema" not in _cached:
        _cached["schema"], _cached["warnings"], _cached["errors"] = generate_schema()
    return _cached["schema"], _cached["warnings"], _cached["errors"]


def schema_operations(schema):
    """Yield (method, path, operation) for every operation in the schema."""
    methods = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
    for path, item in schema.get("paths", {}).items():
        for method, operation in item.items():
            if method.lower() in methods:
                yield method.upper(), path, operation


def structure_of(path):
    """Normalize a path so template params and schema params compare equal."""
    return re.sub(r"\{[^}/]+\}", "{p}", path)


def segments_match(template_path, schema_path):
    """Segment-wise match: literals must agree, params match anything.

    A template hardcodes enum segments the schema documents as parameters
    (``.../comments/{id}/hide`` vs ``.../comments/{public_id}/{action}``),
    so plain string equality would false-positive on every {action} route.
    """
    ours = template_path.strip("/").split("/")
    theirs = schema_path.strip("/").split("/")
    if len(ours) != len(theirs):
        return False
    for left, right in zip(ours, theirs):
        left_param = left.startswith("{") and left.endswith("}")
        right_param = right.startswith("{") and right.endswith("}")
        if left_param or right_param:
            continue
        if left != right:
            return False
    return True


def resolve_template_url(raw, filename):
    """Resolve template variables to a concrete /api/v1/... path template."""
    url = raw.split("?", 1)[0].split("#", 1)[0].strip()
    bare = re.fullmatch(r"\{\{\s*(\w+)\s*\}\}", url)
    if bare:
        per_file = CONTEXT_URLS_BY_FILE.get(f"{filename}::{bare.group(1)}")
        if per_file:
            return per_file
        if bare.group(1) in CONTEXT_URLS and "::" not in bare.group(1):
            return CONTEXT_URLS[bare.group(1)]
    for name, concrete in CONTEXT_URLS.items():
        if "::" in name:
            continue
        url = url.replace("{{ %s }}" % name, concrete)
        url = url.replace("{{%s}}" % name, concrete)
    url = re.sub(r"\{\{[^}]+\}\}", "{p}", url)
    return url


def collect_template_pairs():
    """Every (method, path) the templates can send.

    api-forms.js only sends elements carrying data-api-method (default POST);
    a bare data-api-url is a read/poll URL consumed by page JS, so it counts
    as GET. consequences.js additionally drives data-execute-url (with
    data-execute-method), data-consequences-url and data-publish-url, and
    voting.js drives GET+POST+PUT on the ballot base URL.
    """
    pairs = set()
    tag_re = re.compile(r"<[a-zA-Z][^>]*>", re.S)
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        filename = str(path.relative_to(TEMPLATES_DIR)).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        for tag in tag_re.findall(text):
            url_match = re.search(r'data-api-url="([^"]+)"', tag)
            if url_match:
                resolved = resolve_template_url(url_match.group(1), filename)
                method_match = re.search(r'data-api-method="([^"]+)"', tag)
                if method_match:
                    pairs.add((method_match.group(1).upper(), resolved))
                else:
                    # No data-api-method: api-forms.js ignores the control, so
                    # the URL is a read/poll for page JS (command-center,
                    # progress, voting, outbox link) and counts as GET.
                    pairs.add(("GET", resolved))
                    if filename == "community/ballot.html":
                        # voting.js: POST creates the ballot, PUT submits it.
                        pairs.add(("POST", resolved))
                        pairs.add(("PUT", resolved.rstrip("/") + "/{p}"))
            exec_match = re.search(r'data-execute-url="([^"]+)"', tag)
            if exec_match:
                exec_method = re.search(r'data-execute-method="([^"]+)"', tag)
                method = exec_method.group(1).upper() if exec_method else "POST"
                pairs.add((method, resolve_template_url(exec_match.group(1), filename)))
            for attr in ("data-consequences-url", "data-publish-url", "data-progress-api"):
                extra = re.search(attr + r'="([^"]+)"', tag)
                if extra:
                    pairs.add(("POST" if "progress" not in attr else "GET",
                               resolve_template_url(extra.group(1), filename)))
    # Outbox read link resolves per page (event outbox, or platform outbox for
    # admins); both are GET operations in the schema.
    pairs.add(("GET", "/api/v1/admin/outbox"))
    return {(method, structure_of(url)) for method, url in pairs if "/api/v1/" in url}


def strip_js_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def collect_js_pairs():
    """Every (method, /api/v1/... literal) hardcoded in src/static/js/."""
    pairs = set()
    literal_re = re.compile(r"""['"](/api/v1/[^'"]*)['"]""")
    method_re = re.compile(r"""method:\s*['"]([A-Za-z]+)['"]""")
    call_re = re.compile(r"""(?:request|send)\(\s*['"]([A-Za-z]+)['"]""")
    for path in sorted(STATIC_JS_DIR.glob("*.js")):
        text = strip_js_comments(path.read_text(encoding="utf-8"))
        for match in literal_re.finditer(text):
            url = match.group(1).split("?", 1)[0]
            window = text[max(0, match.start() - 400): match.end() + 120]
            method_match = method_re.search(window) or call_re.search(window)
            method = method_match.group(1).upper() if method_match else "GET"
            pairs.add((method, structure_of(url)))
    return pairs


class StrictSchemaTest(SimpleTestCase):
    def test_schema_validates_with_zero_warnings(self):
        schema, warnings, errors = cached_generation()
        validate_schema(schema)  # raises on OpenAPI non-compliance
        unexpected_errors = [
            message for message in errors
            if not any(fragment in message for fragment in KNOWN_GENERATOR_ERROR_FRAGMENTS)
        ]
        self.assertEqual(
            warnings,
            [],
            "Schema generation produced warnings (fix the serializers/views):\n"
            + "\n".join(warnings),
        )
        self.assertEqual(
            unexpected_errors,
            [],
            "Schema generation produced errors beyond the allow-listed in-flight "
            "tour views (see KNOWN_GENERATOR_ERROR_FRAGMENTS):\n" + "\n".join(unexpected_errors),
        )


class SchemaDriftTest(SimpleTestCase):
    def test_committed_yaml_matches_generated_schema(self):
        schema, _, _ = cached_generation()
        with OPENAPI_PATH.open(encoding="utf-8") as handle:
            committed = yaml.safe_load(handle)
        self.assertEqual(
            committed,
            schema,
            REGENERATE_HINT + f"\nPaths only in code: "
            f"{sorted(set(schema.get('paths', {})) - set(committed.get('paths', {})))}"
            f"\nPaths only in {OPENAPI_PATH.name}: "
            f"{sorted(set(committed.get('paths', {})) - set(schema.get('paths', {})))}",
        )


class UiParityTest(SimpleTestCase):
    def test_every_ui_action_matches_a_schema_operation(self):
        schema, _, _ = cached_generation()
        operations = [(method, path) for method, path, _ in schema_operations(schema)]
        ui_pairs = (collect_template_pairs() | collect_js_pairs()) - NON_API_ALLOW_LIST
        unmatched = sorted(
            (method, path)
            for method, path in ui_pairs
            if not any(
                method == op_method and segments_match(path, op_path)
                for op_method, op_path in operations
            )
        )
        self.assertEqual(
            unmatched,
            [],
            "UI actions with no documented API operation (add the endpoint or, "
            "only if it is genuinely not an API call, allow-list it in "
            "NON_API_ALLOW_LIST with a comment):\n"
            + "\n".join(f"{method} {path}" for method, path in unmatched)
            + "\n" + REGENERATE_HINT,
        )


class OperationCompletenessTest(SimpleTestCase):
    def test_every_operation_is_documented(self):
        schema, _, _ = cached_generation()
        missing_id, missing_summary, missing_2xx, envelope_gaps = [], [], [], []
        for method, path, operation in schema_operations(schema):
            label = f"{method} {path} ({operation.get('operationId', '?')})"
            operation_id = operation.get("operationId", "")
            if not operation_id:
                missing_id.append(label)
                continue
            if not (operation.get("summary") or operation.get("description")):
                if operation_id not in TOUR_INFLIGHT_OPERATION_IDS:
                    missing_summary.append(label)
            responses = operation.get("responses") or {}
            if not self._has_success_schema(responses, path):
                missing_2xx.append(label)
            for code, response in responses.items():
                if str(code).startswith(("4", "5")) and not self._is_envelope(response):
                    if (method, path) not in ENVELOPE_EXCEPTIONS:
                        envelope_gaps.append(f"{label} -> {code}")
        self.assertEqual(missing_id, [], "Operations without operationId:\n" + "\n".join(missing_id))
        self.assertEqual(
            missing_summary, [], "Operations without summary/description:\n" + "\n".join(missing_summary)
        )
        self.assertEqual(
            missing_2xx,
            [],
            "Operations without a 2xx response carrying a schema "
            "(204 No Content and text/csv downloads are exempt by rule):\n" + "\n".join(missing_2xx),
        )
        self.assertEqual(
            envelope_gaps,
            [],
            "Error responses not using the shared ErrorEnvelope component "
            "(shrink ENVELOPE_EXCEPTIONS, never grow it):\n" + "\n".join(envelope_gaps),
        )

    @staticmethod
    def _has_success_schema(responses, path):
        for code, response in responses.items():
            if not str(code).startswith("2"):
                continue
            if str(code) == "204":
                return True  # No Content has no body by definition.
            content = (response or {}).get("content") or {}
            if not content:
                # File downloads (HttpResponse CSVs) bypass DRF content
                # negotiation per BUILD-SPEC section 16; the bytes are the body.
                if "/exports/" in path or path.endswith(".csv"):
                    return True
                continue
            for media_type, body in content.items():
                if not media_type.startswith("application/json"):
                    return True  # Non-JSON download with documented content.
                if body and body.get("schema"):
                    return True
        return False

    @staticmethod
    def _is_envelope(response):
        try:
            content = (response or {}).get("content") or {}
            for body in content.values():
                ref = ((body or {}).get("schema") or {}).get("$ref", "")
                if ref.startswith(ERROR_ENVELOPE_REF):
                    return True
        except AttributeError:
            pass
        return False


class DocsCoverageTest(SimpleTestCase):
    def test_every_operation_id_appears_in_api_md(self):
        schema, _, _ = cached_generation()
        prose = API_MD_PATH.read_text(encoding="utf-8")
        missing = sorted(
            {operation.get("operationId", "") for _, _, operation in schema_operations(schema)}
            - {""}
            - {op for op in self._mentioned(prose, schema)}
        )
        self.assertEqual(
            missing,
            [],
            "operationIds in the schema but missing from docs/API.md "
            "(extend the operations table there):\n" + "\n".join(missing),
        )

    @staticmethod
    def _mentioned(prose, schema):
        return {
            operation.get("operationId", "")
            for _, _, operation in schema_operations(schema)
            if operation.get("operationId", "") and operation.get("operationId", "") in prose
        }
