"""Results API: preview, publish, public results, decision record, feedback.

Thin views: validate the request, delegate to services, return the envelope.
"""
from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.errors import ApiError
from events.policy import get_event_by_slug, is_organizer
from results import consequences as consequence_services
from results import services
from results.policy import (
    can_see_feedback,
    get_publication,
    require_organizer,
)



from core.schema import error_responses
from rest_framework import serializers

TAGS = ["results"]


# --- serializers ---------------------------------------------------------


class ResultRowSerializer(serializers.Serializer):
    """One ranked project. Judge identities are not part of this shape."""

    project_id = serializers.CharField()
    title = serializers.CharField()
    team = serializers.CharField(allow_blank=True)
    track = serializers.CharField(allow_null=True, allow_blank=True)
    n_reviews = serializers.IntegerField(help_text="Reviews actually included.")
    raw_mean = serializers.FloatField(allow_null=True, help_text="Unadjusted mean score.")
    normalized = serializers.FloatField(
        allow_null=True, help_text="Judge-offset adjusted score; null under 'raw'."
    )
    rank = serializers.CharField(allow_null=True,
                                 help_text="Official rank for this event's ranking method.")
    rank_raw = serializers.IntegerField(allow_null=True)
    rank_norm = serializers.IntegerField(allow_null=True)
    rank_bt = serializers.IntegerField(allow_null=True)
    live_strength = serializers.FloatField(allow_null=True,
                                          help_text="Live pairwise strength, when in use.")
    rank_live = serializers.IntegerField(allow_null=True)
    rank_low = serializers.IntegerField(allow_null=True)
    rank_high = serializers.IntegerField(allow_null=True)
    score_low = serializers.FloatField(allow_null=True)
    score_high = serializers.FloatField(allow_null=True)
    p_first = serializers.FloatField(allow_null=True)
    p_top = serializers.FloatField(allow_null=True)
    p_above_next = serializers.FloatField(allow_null=True)
    tied_with_next = serializers.BooleanField()
    tied_with_previous = serializers.BooleanField()
    n_comparisons = serializers.IntegerField()
    status = serializers.CharField(
        help_text="'ranked', 'draft', 'withdrawn', 'disqualified', 'superseded', or an "
                  "'unranked_*' reason."
    )
    status_reason = serializers.CharField(allow_null=True, allow_blank=True)


class AwardSerializer(serializers.Serializer):
    """A prize actually awarded, with the place and the reason it was allocated there."""

    prize_id = serializers.CharField()
    prize = serializers.CharField(allow_blank=True)
    place = serializers.IntegerField(allow_null=True)
    project_id = serializers.CharField(allow_null=True, allow_blank=True)
    project = serializers.CharField(allow_blank=True)
    team_id = serializers.CharField(allow_null=True, allow_blank=True)
    team = serializers.CharField(allow_blank=True)
    track = serializers.CharField(allow_null=True, allow_blank=True)
    score = serializers.FloatField(allow_null=True)
    rank = serializers.IntegerField(allow_null=True)
    reason = serializers.CharField(allow_blank=True)
    note = serializers.CharField(allow_null=True, allow_blank=True)


class UnawardedPrizeSerializer(serializers.Serializer):
    """A prize that could not be awarded, and why. Shown rather than omitted."""

    prize_id = serializers.CharField()
    prize = serializers.CharField(allow_blank=True)
    reason = serializers.CharField(allow_blank=True)
    place = serializers.IntegerField(allow_null=True)
    projects = serializers.ListField(child=serializers.CharField())


class RankUncertaintySerializer(serializers.Serializer):
    available = serializers.BooleanField()
    replicates = serializers.IntegerField()
    seed = serializers.IntegerField()
    level = serializers.FloatField()
    top_k = serializers.IntegerField()
    sigma = serializers.FloatField()
    df = serializers.IntegerField()
    summary = serializers.CharField()
    assumption = serializers.CharField()
    reason = serializers.CharField(allow_blank=True)
    tied_pairs = serializers.ListField(
        child=serializers.ListField(child=serializers.CharField())
    )
    groups = serializers.ListField(
        child=serializers.ListField(child=serializers.CharField())
    )


class PublicRankUncertaintySerializer(serializers.Serializer):
    available = serializers.BooleanField()
    replicates = serializers.IntegerField()
    level = serializers.FloatField()
    top_k = serializers.IntegerField()
    df = serializers.IntegerField()
    summary = serializers.CharField()
    assumption = serializers.CharField()
    reason = serializers.CharField(allow_blank=True)
    tied_pairs = serializers.ListField(
        child=serializers.ListField(child=serializers.CharField())
    )
    groups = serializers.ListField(
        child=serializers.ListField(child=serializers.CharField())
    )


class ResultsPreviewSerializer(serializers.Serializer):
    """The engine output an organizer sees before publishing. Nothing is stored."""

    method = serializers.CharField(help_text="'raw', 'normalized' or 'pairwise'.")
    lam = serializers.FloatField(allow_null=True, help_text="Shrinkage penalty actually used.")
    lambda_choice = serializers.DictField(
        allow_null=True,
        help_text="Cross-validated lambda choice, or null when lambda was fixed. Carries "
                  "cv_rmse, baseline_rmse, folds and n.",
    )
    input_digest = serializers.CharField(
        help_text="Digest of the exact inputs. Comparing it with the digest a later publish "
                  "reports is how an organizer knows the data has not moved."
    )
    rows = ResultRowSerializer(many=True)
    uncertainty = RankUncertaintySerializer()
    live_pairwise = serializers.DictField(
        help_text="Pairwise component counts and whether live ballots were used."
    )
    judge_rows = serializers.ListField(
        child=serializers.DictField(), help_text="Per-judge offset and load; organizer only."
    )
    awards = AwardSerializer(many=True)
    unawarded = UnawardedPrizeSerializer(many=True)
    diagnostics = serializers.DictField(
        help_text="under_reviewed, single_review_judges, constant_scorers, n_components."
    )
    outliers = serializers.ListField(
        child=serializers.DictField(), help_text="Flagged reviews. Flagged, never auto-excluded."
    )
    spread = serializers.DictField(help_text="Score spread before and after normalization.")
    n_included = serializers.IntegerField()
    n_excluded = serializers.IntegerField()
    params = serializers.DictField(help_text="Every method parameter, for the record.")


class PublicationHistoryEntrySerializer(serializers.Serializer):
    version = serializers.IntegerField()
    published_at = serializers.DateTimeField()
    note = serializers.CharField(allow_blank=True)
    input_digest = serializers.CharField()


class PublicResultRowSerializer(serializers.Serializer):
    rank = serializers.CharField(allow_null=True)
    project_id = serializers.CharField()
    title = serializers.CharField()
    team = serializers.CharField(allow_blank=True)
    track = serializers.CharField(allow_null=True, allow_blank=True)
    n_reviews = serializers.IntegerField(allow_null=True)
    raw_mean = serializers.FloatField(allow_null=True)
    normalized = serializers.FloatField(allow_null=True)
    live_strength = serializers.FloatField(allow_null=True)
    rank_live = serializers.IntegerField(allow_null=True)
    n_comparisons = serializers.IntegerField()
    status = serializers.CharField()
    status_reason = serializers.CharField(allow_null=True, allow_blank=True)
    rank_low = serializers.IntegerField(allow_null=True)
    rank_high = serializers.IntegerField(allow_null=True)
    score_low = serializers.FloatField(allow_null=True)
    score_high = serializers.FloatField(allow_null=True)
    p_first = serializers.FloatField(allow_null=True)
    p_top = serializers.FloatField(allow_null=True)
    p_above_next = serializers.FloatField(allow_null=True)
    tied_with_next = serializers.BooleanField()
    tied_with_previous = serializers.BooleanField()


class PublicResultsSerializer(serializers.Serializer):
    """The public projection of the newest publication: awards, no judge data."""

    pub_id = serializers.CharField()
    version = serializers.IntegerField()
    published_at = serializers.DateTimeField()
    supersedes_version = serializers.IntegerField(allow_null=True)
    note = serializers.CharField(allow_blank=True)
    method = serializers.CharField()
    lam = serializers.FloatField(allow_null=True)
    lambda_source = serializers.CharField()
    rows = PublicResultRowSerializer(many=True)
    uncertainty = PublicRankUncertaintySerializer()
    awards = AwardSerializer(many=True)
    unawarded = UnawardedPrizeSerializer(many=True)
    history = PublicationHistoryEntrySerializer(many=True)


class PublishSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=300)
    acknowledge_unranked = serializers.BooleanField(
        required=False,
        help_text="Required when some projects are unranked, so the organizer is recorded "
                  "as having seen that before publishing.",
    )
    expected_digest = serializers.CharField(required=False, allow_blank=False, max_length=64)


class ConsequenceRequestSerializer(serializers.Serializer):
    action = serializers.ChoiceField(
        choices=("publish", "disqualify", "exclude_review", "include_review")
    )
    target = serializers.CharField(required=False, allow_blank=False, allow_null=True)


class RankChangeSerializer(serializers.Serializer):
    project = serializers.CharField()
    title = serializers.CharField()
    before = serializers.CharField()
    after = serializers.CharField()


class AwardChangeSerializer(serializers.Serializer):
    prize = serializers.CharField()
    prize_name = serializers.CharField()
    before = serializers.ListField(child=serializers.DictField())
    after = serializers.ListField(child=serializers.DictField())


class ConsequenceResponseSerializer(serializers.Serializer):
    action = serializers.CharField()
    target = serializers.CharField(allow_null=True)
    basis_digest = serializers.CharField()
    rank_changes = RankChangeSerializer(many=True)
    award_changes = AwardChangeSerializer(many=True)
    winner_before = serializers.DictField(allow_null=True)
    winner_after = serializers.DictField(allow_null=True)
    n_rank_changes = serializers.IntegerField()
    n_award_changes = serializers.IntegerField()
    sentence = serializers.CharField()
    certainty = serializers.CharField(allow_blank=True)
    top_tied = serializers.BooleanField()
    top_tie_warning = serializers.CharField(allow_blank=True)


class PublicationCreatedSerializer(serializers.Serializer):
    pub_id = serializers.CharField()
    version = serializers.IntegerField(help_text="Increments; a publication is immutable.")
    published_at = serializers.DateTimeField()
    method = serializers.CharField()
    input_digest = serializers.CharField()
    note = serializers.CharField(allow_blank=True)


class PublicationDecisionRecordSerializer(serializers.Serializer):
    """Organizer-only record of what was decided, on what inputs, with what limits."""

    pub_id = serializers.CharField()
    version = serializers.IntegerField()
    published_at = serializers.DateTimeField()
    published_by = serializers.CharField(allow_null=True, allow_blank=True)
    method = serializers.CharField()
    params = serializers.DictField()
    input_digest = serializers.CharField()
    rule_plain = serializers.CharField(help_text="The ranking rule in plain sentences.")
    included_reviews = serializers.ListField(child=serializers.DictField())
    excluded_reviews = serializers.ListField(child=serializers.DictField())
    project_snapshot = serializers.ListField(child=serializers.DictField())
    project_snapshot_backfilled = serializers.BooleanField(
        help_text="True when the snapshot was reconstructed after the fact rather than "
                  "captured at publish time, which weakens the historical claim."
    )
    prizes = serializers.ListField(child=serializers.DictField())
    one_prize_per_team = serializers.BooleanField(allow_null=True)
    awards = AwardSerializer(many=True)
    unawarded = UnawardedPrizeSerializer(many=True)
    interventions = serializers.ListField(
        child=serializers.DictField(),
        help_text="Window changes, exclusions and batches from the audit log, in order.",
    )
    limitations = serializers.ListField(child=serializers.CharField())
    supersedes = serializers.CharField(allow_null=True, allow_blank=True)
    note = serializers.CharField(allow_blank=True)


class ReplayVerdictSerializer(serializers.Serializer):
    verdict = serializers.CharField()
    matches = serializers.BooleanField()
    detail = serializers.CharField(allow_blank=True)


class UnchangedVerdictSerializer(serializers.Serializer):
    verdict = serializers.CharField()
    matches = serializers.BooleanField()
    differences = serializers.ListField(child=serializers.CharField())


class PublicationVerificationSerializer(serializers.Serializer):
    """Recompute the stored inputs and compare, then compare with live data again.

    Two separate claims on purpose: a publication can replay identically while the
    live data has since changed, or the other way round.
    """

    pub_id = serializers.CharField()
    verdict = serializers.CharField(help_text="'identical' or 'differs'.")
    detail = serializers.CharField(allow_blank=True)
    reproducible = ReplayVerdictSerializer(
        help_text="Re-running the engine on the stored input snapshot."
    )
    unchanged_since_publication = UnchangedVerdictSerializer(
        help_text="Whether the live data still digests to the same value."
    )
    rows_match = serializers.BooleanField()
    awards_match = serializers.BooleanField(allow_null=True)
    digest_match = serializers.BooleanField()
    stored_inputs_match = serializers.BooleanField()
    stored_digest = serializers.CharField()
    live_digest = serializers.CharField(allow_null=True)
    reproducible_verdict = serializers.CharField(help_text="Echo of reproducible.verdict.")
    reproducible_detail = serializers.CharField(allow_blank=True)
    unchanged_verdict = serializers.CharField(help_text="Echo of unchanged_since_publication.verdict.")
    difference_text = serializers.CharField(help_text="Field-level differences, as text.")


class FeedbackReleaseSerializer(serializers.Serializer):
    pub_id = serializers.CharField()
    feedback_released_at = serializers.DateTimeField(allow_null=True)


class ProjectFeedbackSerializer(serializers.Serializer):
    """De-attributed score summary for one team.

    ``comments`` is always empty. A judge's written note is private to that judge
    and the organizers under its collection contract and is never released here;
    shipping written team feedback would need its own separately consented field.
    """

    pub_id = serializers.CharField()
    feedback_released = serializers.BooleanField()
    official_score = serializers.FloatField(allow_null=True)
    rank = serializers.CharField(allow_null=True)
    n_reviews = serializers.IntegerField()
    per_criterion = serializers.DictField(
        child=serializers.FloatField(), help_text="Criterion key to mean score."
    )
    comments = serializers.ListField(child=serializers.CharField())


def _get_event(slug: str):
    event = get_event_by_slug(slug)
    if event is None:
        raise ApiError("event_not_found", f"No event with slug {slug!r}.", status_code=404)
    return event


class ResultsPreviewView(APIView):
    """``GET /events/{slug}/results/preview`` — organizer only."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="results_preview",
        summary="Preview the ranking without publishing anything (organizer).",
        description="Runs the engine over the current data and returns the rows, awards and "
                    "diagnostics a publish would produce. Nothing is stored. Compare "
                    "input_digest here with the digest a publish reports to know whether the "
                    "data has moved in between.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={200: ResultsPreviewSerializer, **error_responses(401, 403, 404, 409)},
        tags=TAGS,
    )
    def get(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        require_organizer(request.user, event)
        data = services.preview(event)
        return Response(data)


class ResultsConsequencesView(APIView):
    """``POST /events/{slug}/results/consequences`` — organizer-only preview."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="results_consequences",
        summary="Preview the exact consequences of a results action (organizer).",
        description="Read-only comparison against the current preview or latest official "
                    "publication. No audit or result data is written.",
        request=ConsequenceRequestSerializer,
        responses={200: ConsequenceResponseSerializer, **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request: Request, slug: str) -> Response:
        serializer = ConsequenceRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = _get_event(slug)
        require_organizer(request.user, event)
        data = consequence_services.consequences(
            event,
            serializer.validated_data["action"],
            serializer.validated_data.get("target"),
        )
        return Response(data)


class ResultsPublishView(APIView):
    """``POST /events/{slug}/results/publish`` — organizer only."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="results_publish",
        summary="Publish results (organizer).",
        description="A publication is immutable. Republishing supersedes the previous "
                    "version rather than editing it, and needs a note explaining why. "
                    "Refused with 409 while judging is open, while any project is unranked "
                    "and acknowledge_unranked was not sent, or when there is no rubric.",
        request=PublishSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={201: PublicationCreatedSerializer,
                   **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        require_organizer(request.user, event)
        serializer = PublishSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pub = services.publish(
            request.user, event, note=serializer.validated_data.get("note", ""),
            acknowledge_unranked=serializer.validated_data.get("acknowledge_unranked", False),
            expected_digest=serializer.validated_data.get("expected_digest"),
        )
        return Response(
            {
                "pub_id": pub.public_id,
                "version": pub.version,
                "published_at": pub.published_at.isoformat(),
                "method": pub.method,
                "input_digest": pub.input_digest,
                "note": pub.note,
            },
            status=201,
        )


class ResultsPublicView(APIView):
    """``GET /events/{slug}/results`` — public after publication; organizers get preview."""

    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="results_public",
        summary="Public results for an event.",
        description="No judge identities, offsets, individual review details or judge notes "
                    "appear here. An organizer calling this route gets the live preview "
                    "instead of the published projection.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={200: PublicResultsSerializer, **error_responses(404)},
        tags=TAGS,
    )
    def get(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        # Organizers get the live preview instead.
        if request.user.is_authenticated and is_organizer(request.user, event):
            data = services.preview(event)
            return Response(data)
        data = services.public_results(event)
        return Response(data)


class PublicationDetailView(APIView):
    """``GET /events/{slug}/results/publications/{pub_id}`` — organizer only, decision record."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="publication_detail",
        summary="The full decision record for one publication (organizer).",
        description="Includes the exact inputs, the interventions from the audit log, the "
                    "prizes and awards, and an explicit limitations list. "
                    "project_snapshot_backfilled says whether the snapshot was captured at "
                    "publish time or reconstructed later.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("pub_id", str, OpenApiParameter.PATH,
                             description="Result publication public id."),
        ],
        responses={200: PublicationDecisionRecordSerializer,
                   **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request: Request, slug: str, pub_id: str) -> Response:
        event = _get_event(slug)
        require_organizer(request.user, event)
        pub = get_publication(event, pub_id)
        return Response(services.decision_record(pub))


class PublicationVerifyView(APIView):
    """``POST /events/{slug}/results/publications/{pub_id}/verify`` — organizer only."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="publication_verify",
        summary="Replay a publication from its stored inputs and compare with live data.",
        description="Two separate claims. `reproducible` re-runs the engine on the snapshot "
                    "stored with the publication. `unchanged_since_publication` re-digests "
                    "the live data. This checks the recorded computation, not authorship: a "
                    "privileged actor who rewrote both the data and the digest would still "
                    "pass. A post-close correction is a new superseding version, never an "
                    "edit to this one.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("pub_id", str, OpenApiParameter.PATH,
                             description="Result publication public id."),
        ],
        responses={200: PublicationVerificationSerializer,
                   **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def post(self, request: Request, slug: str, pub_id: str) -> Response:
        event = _get_event(slug)
        require_organizer(request.user, event)
        pub = get_publication(event, pub_id)
        result = services.verify_publication(pub)
        result["reproducible_verdict"] = result["reproducible"]["verdict"]
        result["reproducible_detail"] = result["reproducible"]["detail"]
        result["unchanged_verdict"] = result["unchanged_since_publication"]["verdict"]
        result["difference_text"] = "\n".join(
            result["unchanged_since_publication"]["differences"]
        ) or "No field-level differences."
        return Response(result)


class FeedbackReleaseView(APIView):
    """``POST|DELETE /events/{slug}/feedback-release`` — organizer only."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="feedback_release",
        summary="Release the score summary to each team (organizer).",
        description="Releases the de-attributed score summary only. A judge's written note "
                    "stays private under its collection contract and is never released; "
                    "shipping written team feedback would need its own separately consented "
                    "field, and none is backfilled from private notes.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={200: FeedbackReleaseSerializer,
                   **error_responses(401, 403, 404, 409)},
        tags=TAGS,
    )
    def post(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        require_organizer(request.user, event)
        pub = services.release_feedback(request.user, event)
        return Response(
            {"pub_id": pub.public_id, "feedback_released_at": pub.feedback_released_at.isoformat()},
            status=200,
        )

    @extend_schema(
        operation_id="feedback_retract",
        summary="Stop releasing the score summary to teams (organizer).",
        description="Reverses the release and is audited. Nothing is deleted: a later "
                    "release shows the same summary again.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={200: FeedbackReleaseSerializer,
                   **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def delete(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        require_organizer(request.user, event)
        pub = services.retract_feedback(request.user, event)
        return Response(
            {"pub_id": pub.public_id, "feedback_released_at": None},
            status=200,
        )


class ProjectFeedbackView(APIView):
    """``GET /events/{slug}/projects/{project_id}/feedback`` — team members after release."""

    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="project_feedback",
        summary="The released score summary for your own project (team members).",
        description="Team members only, and only after the organizer released it; "
                    "otherwise 403 not_released. De-attributed: no judge is named or "
                    "identifiable, and comments is always empty because a judge's written "
                    "note is private to that judge and the organizers.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("project_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
        ],
        responses={200: ProjectFeedbackSerializer,
                   **error_responses(403, 404)},
        tags=TAGS,
    )
    def get(self, request: Request, slug: str, project_id: str) -> Response:
        event = _get_event(slug)
        from projects.models import Project

        project = Project.objects.filter(event=event, public_id=project_id).select_related("team").first()
        if project is None:
            raise ApiError("project_not_found", "No such project.", status_code=404)

        if not can_see_feedback(request.user, event, project):
            raise ApiError(
                "forbidden",
                "Only team members may see their project's feedback.",
                status_code=403,
            )
        # Check if feedback is released (organizers can always see it).
        if not is_organizer(request.user, event):
            from results.models import ResultPublication
            pub = ResultPublication.objects.filter(event=event).order_by("-published_at").first()
            if pub is None or pub.feedback_released_at is None:
                raise ApiError(
                    "not_released",
                    "Feedback has not been released for this event.",
                    status_code=403,
                )
        data = services.project_feedback(event, project)
        return Response(data)
