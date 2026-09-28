"""Regressions found during the final PostgreSQL integrity audit."""
import copy

from django.test import TestCase
from rest_framework.test import APIClient

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
