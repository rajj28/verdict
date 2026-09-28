"""core.showcase: the generated calibration event, its seed and its check page.

The interesting claim this module proves is narrow and checkable: on data whose
true order and whose judge habits are known in advance, the engine recovers both
better than raw means do. The rest of the module keeps the promise honest: the
generator is deterministic, the seed is idempotent and touches nothing else, and
the page is organizer-only and follows the current included reviews.
"""
from __future__ import annotations

import os
from collections import Counter
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from core import showcase
from core.bootstrap import ADMIN_EMAIL, DEMO_EVENT_SLUG, ORGANIZER_EMAIL, bootstrap
from core.calibration_views import calibration_report
from events.models import Event, EventRole, Role
from judging.models import Review
from projects.models import Project
from results import engine

#: Ceiling for the calibration page. The number is fixed by the query plan, not
#: by how many projects, reviews or judges the event has (checked below).
MAX_QUERIES = 24


def engine_reviews(data: dict) -> list[engine.ReviewInput]:
    """The generated scores as engine input, in fixture order."""
    return [
        engine.ReviewInput(f"rev_{index:03d}", row["judge"], row["project"], row["criteria"])
        for index, row in enumerate(data["scores"])
    ]


def fit_generated(data: dict) -> engine.Result:
    """The official fit on the generated data: same engine, same auto lambda."""
    criteria = [
        engine.Criterion(key=key, weight=1.0, min_score=1, max_score=5)
        for key in showcase.CRITERIA
    ]
    return engine.evaluate(
        engine_reviews(data), criteria, lam="auto", target=showcase.REVIEWS_PER_PROJECT,
        method="normalized", projects=[row["id"] for row in data["projects"]],
    )


class GeneratorTests(TestCase):
    """The generator alone: deterministic, self-describing and connected."""

    def test_build_and_truth_are_deterministic(self):
        self.assertEqual(showcase.build(), showcase.build())
        self.assertEqual(showcase.truth(), showcase.truth())

    def test_build_has_the_fixture_shape(self):
        data = showcase.build()
        self.assertEqual(
            sorted(data), ["event", "judges", "projects", "scores", "teams", "tracks"]
        )
        self.assertEqual(data["event"]["id"], "evt_showcase")
        self.assertEqual(data["event"]["name"], "Calibration showcase (synthetic)")
        self.assertIn("synthetic", data["event"]["tagline"].casefold())
        self.assertIn("synthetic", data["event"]["description"].casefold())
        self.assertEqual(len(data["projects"]), showcase.PROJECT_COUNT)
        self.assertEqual(len(data["judges"]), showcase.JUDGE_COUNT)
        self.assertEqual(len(data["teams"]), showcase.PROJECT_COUNT)
        self.assertEqual(
            len(data["scores"]), showcase.PROJECT_COUNT * showcase.REVIEWS_PER_PROJECT
        )

    def test_every_score_covers_the_three_fixture_criteria_in_scale(self):
        for row in showcase.build()["scores"]:
            self.assertEqual(sorted(row["criteria"]), sorted(showcase.CRITERIA))
            for value in row["criteria"].values():
                self.assertIn(value, range(1, 6))

    def test_names_and_emails_are_synthetic_and_do_not_reveal_the_habit(self):
        for index, judge in enumerate(showcase.build()["judges"]):
            self.assertEqual(judge["email"], f"showcase.judge{index + 1:02d}@example.org")
            self.assertNotIn("harsh", judge["name"].casefold())
            self.assertNotIn("generous", judge["name"].casefold())
        self.assertEqual(len({judge["name"] for judge in showcase.build()["judges"]}),
                         showcase.JUDGE_COUNT)

    def test_every_project_gets_three_reviews_and_every_judge_six(self):
        rows = showcase.build()["scores"]
        per_project = Counter(row["project"] for row in rows)
        per_judge = Counter(row["judge"] for row in rows)
        self.assertEqual(set(per_project.values()), {showcase.REVIEWS_PER_PROJECT})
        self.assertEqual(len(per_project), showcase.PROJECT_COUNT)
        self.assertEqual(set(per_judge.values()), {showcase.REVIEWS_PER_JUDGE})
        self.assertEqual(len(per_judge), showcase.JUDGE_COUNT)
        pairs = Counter((row["judge"], row["project"]) for row in rows)
        self.assertEqual(max(pairs.values()), 1, "a judge reviewed one project twice")

    def test_the_design_is_connected(self):
        self.assertEqual(len(engine.components(engine_reviews(showcase.build()))), 1)

    def test_truth_plants_two_harsh_two_generous_and_eight_neutral_judges(self):
        truth = showcase.truth()
        self.assertEqual(len(truth["judge_offsets"]), showcase.JUDGE_COUNT)
        self.assertEqual(len(truth["quality"]), showcase.PROJECT_COUNT)
        habits = Counter(
            showcase.planted_habit(offset) for offset in truth["judge_offsets"].values()
        )
        self.assertEqual(habits["harsh"], 2)
        self.assertEqual(habits["generous"], 2)
        self.assertEqual(habits["neutral"], 8)
        self.assertEqual(min(truth["quality"].values()), showcase.QUALITY_LOW)
        self.assertEqual(max(truth["quality"].values()), showcase.QUALITY_HIGH)
        self.assertIn("routed", truth["design"])

    def test_the_quality_ladder_is_evenly_spread(self):
        ladder = showcase.quality_ladder()
        gaps = [b - a for a, b in zip(ladder, ladder[1:])]
        self.assertEqual(len(ladder), showcase.PROJECT_COUNT)
        self.assertEqual(ladder, tuple(sorted(ladder)))
        self.assertEqual((ladder[0], ladder[-1]), (showcase.QUALITY_LOW, showcase.QUALITY_HIGH))
        # Evenly spread to the stored precision: no gap is more than a hundredth out.
        self.assertLessEqual(round(max(gaps) - min(gaps), 6), 0.01)

    def test_harsh_judges_cover_the_strong_projects_and_generous_the_middling_ones(self):
        data = showcase.build()
        truth = showcase.truth()
        quality = {row["id"]: truth["quality"][row["title"]] for row in data["projects"]}
        planted = truth["judge_offsets"]
        ladder = showcase.quality_ladder()
        # The tiers are the ladder itself: the top slice, the middle slice, the rest.
        strong_floor = ladder[showcase.PROJECT_COUNT - showcase.TIER_SIZE]
        middle_ceiling = ladder[showcase.PROJECT_COUNT - showcase.TIER_SIZE - 1]

        def seen(habit: str) -> set:
            ids = {row["id"] for row in data["judges"]
                   if showcase.planted_habit(planted[row["email"]]) == habit}
            return {quality[row["project"]] for row in data["scores"] if row["judge"] in ids}

        harsh, generous = seen("harsh"), seen("generous")
        self.assertEqual(len(harsh), showcase.TIER_SIZE)
        self.assertEqual(len(generous), showcase.TIER_SIZE)
        self.assertGreaterEqual(min(harsh), strong_floor)
        self.assertLessEqual(max(generous), middle_ceiling)
        self.assertGreater(min(harsh), max(generous))

    def test_normalization_recovers_the_truth_better_than_raw_means(self):
        data = showcase.build()
        truth = showcase.truth()
        result = fit_generated(data)
        titles = {row["id"]: row["title"] for row in data["projects"]}
        ids = sorted(titles)
        true = [truth["quality"][titles[i]] for i in ids]
        raw = [result.raw[i][0] for i in ids]
        normalized = [result.normalized[i] for i in ids]
        self.assertGreater(
            engine.kendall_tau(true, normalized), engine.kendall_tau(true, raw),
            "normalization did not beat raw means on the generated data",
        )
        self.assertEqual(result.diagnostics.n_components, 1)

    def test_the_estimated_offsets_keep_the_planted_sign(self):
        data = showcase.build()
        planted = showcase.truth()["judge_offsets"]
        directory = showcase.judge_directory()
        result = fit_generated(data)
        found = 0
        for judge_id, meta in directory.items():
            offset = planted[meta["email"]]
            if showcase.planted_habit(offset) == "neutral":
                continue
            found += 1
            self.assertEqual(
                (result.fit.offset[judge_id] < 0), (offset < 0),
                f"{judge_id} came back with the wrong sign",
            )
        self.assertEqual(found, 4)


class ShowcaseSeedTests(TestCase):
    """The seed is idempotent, resettable, and only ever touches the showcase."""

    def setUp(self):
        environment = patch.dict(os.environ, {
            "ADMIN_EMAIL": ADMIN_EMAIL, "ADMIN_PASSWORD": "production-admin-password",
        })
        environment.start()
        self.addCleanup(environment.stop)
        # DEMO_MODE off: bootstrap seeds the fixture and the demo event only, so
        # the command under test is the only thing that creates the showcase.
        with override_settings(DEMO_MODE=False):
            bootstrap()
        self.admin = User.objects.get(email=ADMIN_EMAIL)
        self.organizer = User.objects.get(email=ORGANIZER_EMAIL)

    def counts_by_event(self) -> dict:
        return {
            event.slug: {
                "projects": Project.objects.filter(event=event).count(),
                "reviews": Review.objects.filter(event=event).count(),
                "roles": EventRole.objects.filter(event=event).count(),
            }
            for event in Event.objects.exclude(source_id=showcase.SHOWCASE_SOURCE_ID)
        }

    def test_seeding_twice_changes_nothing_and_says_so(self):
        first = StringIO()
        call_command("seed_showcase", stdout=first)
        counts = {
            "projects": Project.objects.filter(event__source_id=showcase.SHOWCASE_SOURCE_ID).count(),
            "reviews": Review.objects.filter(event__source_id=showcase.SHOWCASE_SOURCE_ID).count(),
        }
        second = StringIO()
        call_command("seed_showcase", stdout=second)
        self.assertIn("Imported the calibration showcase", first.getvalue())
        self.assertIn("Nothing to do", second.getvalue())
        self.assertEqual(counts, {
            "projects": showcase.PROJECT_COUNT,
            "reviews": showcase.PROJECT_COUNT * showcase.REVIEWS_PER_PROJECT,
        })
        self.assertEqual(
            Project.objects.filter(event__source_id=showcase.SHOWCASE_SOURCE_ID).count(),
            showcase.PROJECT_COUNT,
        )
        self.assertEqual(
            Review.objects.filter(event__source_id=showcase.SHOWCASE_SOURCE_ID).count(),
            showcase.PROJECT_COUNT * showcase.REVIEWS_PER_PROJECT,
        )

    def test_the_seeded_event_is_closed_unpublished_and_organizer_owned(self):
        call_command("seed_showcase", stdout=StringIO())
        event = Event.objects.get(source_id=showcase.SHOWCASE_SOURCE_ID)
        self.assertEqual(event.slug, showcase.SHOWCASE_SLUG)
        self.assertIsNotNone(event.judging_close_at)
        self.assertIsNotNone(event.scoring_locked_at)
        # Nothing published, so no feedback has been released either.
        self.assertEqual(event.result_publications.count(), 0)
        self.assertTrue(
            event.roles.filter(user=self.organizer, role=Role.ORGANIZER).exists()
        )
        self.assertEqual(event.prizes.count(), 2)

    def test_the_fixture_and_demo_events_are_untouched(self):
        before = self.counts_by_event()
        call_command("seed_showcase", stdout=StringIO())
        self.assertEqual(self.counts_by_event(), before)
        self.assertIn(DEMO_EVENT_SLUG, before)
        self.assertIn("sample-hack-2026", before)

    def test_reset_restores_the_event_after_a_change(self):
        call_command("seed_showcase", stdout=StringIO())
        event = Event.objects.get(source_id=showcase.SHOWCASE_SOURCE_ID)
        project = Project.objects.filter(event=event).first()
        project.title = "Tampered title"
        project.save(update_fields=["title"])
        Review.objects.filter(event=event).first().delete()
        reviews = Review.objects.filter(event=event).count()
        self.assertEqual(reviews, showcase.PROJECT_COUNT * showcase.REVIEWS_PER_PROJECT - 1)
        out = StringIO()
        call_command("seed_showcase", "--reset", stdout=out)
        self.assertIn("Imported the calibration showcase", out.getvalue())
        event = Event.objects.get(source_id=showcase.SHOWCASE_SOURCE_ID)
        self.assertFalse(Project.objects.filter(event=event, title="Tampered title").exists())
        self.assertEqual(Review.objects.filter(event=event).count(),
                         showcase.PROJECT_COUNT * showcase.REVIEWS_PER_PROJECT)
        self.assertTrue(event.roles.filter(user=self.organizer, role=Role.ORGANIZER).exists())
        self.assertIsNotNone(event.judging_close_at)

    def test_reset_refuses_any_other_slug(self):
        call_command("seed_showcase", stdout=StringIO())
        before = Project.objects.filter(event__slug="sample-hack-2026").count()
        with self.assertRaises(CommandError):
            call_command("seed_showcase", "--reset", "--slug", "sample-hack-2026", stdout=StringIO())
        self.assertEqual(Project.objects.filter(event__slug="sample-hack-2026").count(), before)
        self.assertTrue(Event.objects.filter(source_id=showcase.SHOWCASE_SOURCE_ID).exists())

    def test_the_command_refuses_to_run_without_an_administrator(self):
        User.objects.filter(email=ADMIN_EMAIL).update(is_admin=False, is_active=False)
        with self.assertRaises(CommandError):
            call_command("seed_showcase", stdout=StringIO())


class BootstrapShowcaseTests(TestCase):
    """bootstrap() seeds the showcase in demo mode and never in production."""

    @override_settings(DEMO_MODE=True)
    def test_demo_mode_seeds_the_showcase_once(self):
        bootstrap()
        event = Event.objects.get(source_id=showcase.SHOWCASE_SOURCE_ID)
        self.assertEqual(event.slug, showcase.SHOWCASE_SLUG)
        organizer = User.objects.get(email=ORGANIZER_EMAIL)
        self.assertTrue(event.roles.filter(user=organizer, role=Role.ORGANIZER).exists())
        counts = (Project.objects.filter(event=event).count(),
                  Review.objects.filter(event=event).count())
        report = bootstrap()
        self.assertFalse(report.showcase.imported)
        self.assertEqual(
            counts,
            (Project.objects.filter(event=event).count(),
             Review.objects.filter(event=event).count()),
        )

    @override_settings(DEMO_MODE=True)
    def test_demo_mode_leaves_the_fixture_and_demo_events_alone(self):
        bootstrap()
        snapshot = {
            event.slug: (Project.objects.filter(event=event).count(),
                         Review.objects.filter(event=event).count(),
                         EventRole.objects.filter(event=event).count())
            for event in Event.objects.exclude(source_id=showcase.SHOWCASE_SOURCE_ID)
        }
        bootstrap()
        self.assertEqual(
            snapshot,
            {event.slug: (Project.objects.filter(event=event).count(),
                          Review.objects.filter(event=event).count(),
                          EventRole.objects.filter(event=event).count())
             for event in Event.objects.exclude(source_id=showcase.SHOWCASE_SOURCE_ID)},
        )
        self.assertEqual(sorted(snapshot), ["demo-hack", "sample-hack-2026"])

    def test_production_mode_seeds_nothing(self):
        environment = patch.dict(os.environ, {
            "ADMIN_EMAIL": ADMIN_EMAIL, "ADMIN_PASSWORD": "production-admin-password",
        })
        environment.start()
        self.addCleanup(environment.stop)
        report = bootstrap()
        self.assertIsNone(report.showcase)
        self.assertFalse(Event.objects.filter(source_id=showcase.SHOWCASE_SOURCE_ID).exists())
        self.assertFalse(Event.objects.filter(slug=showcase.SHOWCASE_SLUG).exists())


@override_settings(DEMO_MODE=True)
class CalibrationPageTests(TestCase):
    """The page: organizer only, four sections, no N+1 queries."""

    @classmethod
    def setUpTestData(cls):
        bootstrap()
        cls.event = Event.objects.get(source_id=showcase.SHOWCASE_SOURCE_ID)
        cls.organizer = User.objects.get(email=ORGANIZER_EMAIL)
        cls.judge = User.objects.get(email="showcase.judge01@example.org")
        cls.participant = User.objects.get(email="showcase.member01@example.org")
        cls.url = f"/manage/{showcase.SHOWCASE_SLUG}/calibration"

    def test_the_organizer_sees_the_four_sections(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        for heading in ("1. Judge habits", "2. Order recovery", "3. Top 3", "4. Verdict"):
            self.assertContains(page, heading)
        self.assertContains(page, "5. What this does and does not prove")
        self.assertContains(page, "NORMALIZATION-PROOF.md")

    def test_the_page_shows_the_planted_design_and_the_true_winner(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.url)
        report = calibration_report(self.event)
        self.assertContains(page, "Synthetic event")
        # The template escapes the apostrophe, so match an unescaped fragment.
        self.assertContains(page, "routed by planted habit")
        self.assertContains(page, report["order"]["winner"]["title"])
        # The verdict is computed, not written into the template.
        self.assertContains(page, "Kendall tau")

    def test_the_page_reports_recovery_on_the_seeded_data(self):
        report = calibration_report(self.event)
        self.assertEqual(report["n_reviews"], showcase.PROJECT_COUNT * showcase.REVIEWS_PER_PROJECT)
        self.assertEqual(report["components"], 1)
        self.assertEqual(report["planted_count"], 4)
        self.assertEqual(report["signs_ok"], 4)
        self.assertGreater(report["order"]["tau_norm"], report["order"]["tau_raw"])
        self.assertLess(report["order"]["off_norm"], report["order"]["off_raw"])
        self.assertGreater(report["offset_rho"], 0.5)

    def test_a_judge_and_a_participant_are_refused(self):
        for user in (self.judge, self.participant):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_an_anonymous_reader_is_sent_to_the_login_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_another_event_is_not_found(self):
        self.client.force_login(self.organizer)
        for slug in ("sample-hack-2026", DEMO_EVENT_SLUG, "no-such-event"):
            self.assertEqual(self.client.get(f"/manage/{slug}/calibration").status_code, 404)

    def test_the_overview_links_the_page_only_for_the_showcase(self):
        self.client.force_login(self.organizer)
        self.assertContains(self.client.get(f"/manage/{showcase.SHOWCASE_SLUG}/"), self.url)
        self.assertNotContains(
            self.client.get(f"/manage/{DEMO_EVENT_SLUG}/"), f"/manage/{DEMO_EVENT_SLUG}/calibration"
        )

    def test_the_page_follows_an_exclusion(self):
        from judging import services as judging_services

        self.client.force_login(self.organizer)
        before = calibration_report(self.event)
        review = Review.objects.filter(event=self.event, judge__public_id="jdg_sc01").first()
        judging_services.exclude_review(self.organizer, review, "Rehearsal exclusion.")
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        after = calibration_report(self.event)
        self.assertEqual(after["n_reviews"], before["n_reviews"] - 1)
        self.assertEqual(after["n_excluded"], 1)
        harsh = [row for row in after["judges"] if row["judge_id"] == "jdg_sc01"]
        self.assertEqual(len(harsh), 1, "the excluded judge's offset disappeared")
        self.assertEqual(harsh[0]["n"], 5)
        self.assertNotEqual(after["order"]["tau_raw"], before["order"]["tau_raw"])

    def test_the_page_costs_a_fixed_number_of_queries(self):
        self.client.force_login(self.organizer)
        with CaptureQueriesContext(connection) as first:
            self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertLessEqual(len(first), MAX_QUERIES)
        for index in range(3):
            team = self.event.teams.create(name=f"Extra team {index}")
            Project.objects.create(event=self.event, team=team, title=f"Extra project {index}")
        with CaptureQueriesContext(connection) as second:
            self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertLessEqual(len(second), MAX_QUERIES)
