"""Regressions found during the final PostgreSQL integrity audit."""
import copy
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from core.bootstrap import ORGANIZER_EMAIL, bootstrap
from events import services as event_services
from events.models import Event
from events.models import Prize
from interop.certificates import verification_code
from projects import services as project_services
from results import services as results_services
from results.models import ResultPublication
from tests import test_publication_policy as publication_tests


class FinalPublicationTests(TestCase):
    setUp = publication_tests.PublicationPolicyTests.setUp
    publish = publication_tests.PublicationPolicyTests.publish

    def test_verify_rejects_policy_columns_that_disagree_with_hashed_inputs(self):
        publication = self.publish()
        client = APIClient()
        client.force_authenticate(self.organizer)
        url = (f"/api/v1/events/{self.event.slug}/results/publications/"
               f"{publication.public_id}/verify")
        self.assertEqual(client.post(url).data["verdict"], "identical")

        original = copy.deepcopy(publication.params)
        for changes in ({"params": {**original, "lam": 999}}, {"method": "raw"},
                        {"params": ["malformed"]}):
            with self.subTest(changes=changes):
                ResultPublication.objects.filter(pk=publication.pk).update(**changes)
                response = client.post(url)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.data["reproducible"]["matches"])
                self.assertTrue(response.data["unchanged_since_publication"]["matches"])
                self.assertIn("policy", response.data["reproducible"]["detail"])
                ResultPublication.objects.filter(pk=publication.pk).update(
                    params=original, method=publication.method)

    def test_verify_malformed_exclusions_fail_closed_without_a_500(self):
        publication = self.publish()
        original = copy.deepcopy(publication.inputs)
        client = APIClient()
        client.force_authenticate(self.organizer)
        url = (f"/api/v1/events/{self.event.slug}/results/publications/"
               f"{publication.public_id}/verify")
        for excluded in (None, [None], [{"review_id": []}], [{"review_id": "x", "criteria": 9}]):
            with self.subTest(excluded=excluded):
                ResultPublication.objects.filter(pk=publication.pk).update(
                    inputs={**original, "excluded": excluded})
                response = client.post(url)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.data["reproducible"]["matches"])
                self.assertIn("malformed", response.data["detail"])
        ResultPublication.objects.filter(pk=publication.pk).update(inputs=original)
        self.assertEqual(client.post(url).data["verdict"], "identical")

    def test_verify_refreshes_cached_event_policy(self):
        publication = self.publish()
        self.assertEqual(results_services.verify_publication(publication)["verdict"], "identical")
        event_services.update_event(self.organizer, self.event, {"reviews_per_project": 7})
        result = results_services.verify_publication(publication)
        self.assertTrue(result["reproducible"]["matches"])
        self.assertFalse(result["unchanged_since_publication"]["matches"])
        self.assertIn("reviews_per_project changed after publication",
                      result["unchanged_since_publication"]["differences"])

    def test_verify_recomputes_instead_of_using_preview_cache(self):
        publication = self.publish()
        results_services.preview(self.event)
        with mock.patch.object(
            results_services.engine,
            "evaluate",
            wraps=results_services.engine.evaluate,
        ) as evaluate:
            result = results_services.verify_publication(publication)
        self.assertEqual(result["verdict"], "identical")
        self.assertEqual(evaluate.call_count, 1)

    def test_public_certificate_verification_reports_superseding_award(self):
        prize = Prize.objects.create(event=self.event, name="Grand Prize")
        first = self.publish()
        certificate_id = f"{first.public_id}.{prize.public_id}.1"
        code = verification_code(self.event, "winner", certificate_id)
        url = f"/certificates/verify/{self.event.slug}/winner/{certificate_id}"
        client = APIClient()
        initial = client.get(url, {"code": code}).json()
        self.assertTrue(initial["valid"])
        self.assertEqual(initial.get("publication_version"), first.version)
        self.assertIsNone(initial.get("superseded_by_version"))
        project_services.disqualify_project(self.organizer, self.project, "Correction")
        second = self.publish("Corrected award")
        replaced = client.get(url, {"code": code}).json()
        self.assertTrue(replaced["valid"])
        self.assertEqual(replaced["publication_version"], first.version)
        self.assertEqual(replaced["superseded_by_version"], second.version)
        self.assertEqual(client.get(url, {"code": "wrong"}).json(),
                         {"valid": False, "kind": "winner"})
        self.assertNotIn("people", replaced)
        self.assertNotIn("owner_ids", replaced)


@override_settings(DEMO_MODE=True)
class ImportedEventVerifyTests(TestCase):
    """A fresh publication of an imported event must verify as identical.

    Publishing reads reviews in database order; Verify replays the stored,
    canonically ordered inputs. The engine sorts its inputs so both paths sum
    floating-point values in the same order and match bit for bit.
    """

    def test_fresh_publication_of_the_organizers_fixture_verifies_identical(self):
        bootstrap()
        event = Event.objects.get(slug="sample-hack-2026")
        organizer = User.objects.get(email=ORGANIZER_EMAIL)
        event_services.close_judging(organizer, event)
        event.refresh_from_db()
        publication = results_services.publish(
            organizer, event, note="Fixture verification", acknowledge_unranked=True)
        result = results_services.verify_publication(publication)
        self.assertEqual(result["verdict"], "identical", result["detail"])
