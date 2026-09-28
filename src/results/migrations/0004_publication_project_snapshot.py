import copy

from django.core.exceptions import ObjectDoesNotExist
from django.db import migrations, models


def backfill_project_snapshots(apps, schema_editor):
    Publication = apps.get_model("results", "ResultPublication")
    Project = apps.get_model("projects", "Project")
    alias = schema_editor.connection.alias

    for publication in Publication.objects.using(alias).select_related("event").all().iterator():
        inputs = copy.deepcopy(publication.inputs) if isinstance(publication.inputs, dict) else {}
        rows = publication.rows if isinstance(publication.rows, list) else []
        listed_ids = {
            row.get("project_id")
            for row in rows
            if isinstance(row, dict) and row.get("project_id")
        }
        for key in ("included", "excluded"):
            review_rows = inputs.get(key, [])
            if not isinstance(review_rows, list):
                review_rows = []
            listed_ids.update(
                item.get("project_id")
                for item in review_rows
                if isinstance(item, dict) and item.get("project_id")
            )
        old_project_ids = inputs.get("projects", [])
        if isinstance(old_project_ids, list):
            listed_ids.update(item for item in old_project_ids if isinstance(item, str))

        projects = {
            project.public_id: project
            for project in Project.objects.using(alias).filter(
                event_id=publication.event_id, public_id__in=listed_ids
            ).select_related("team", "track")
        }
        rows = {
            row.get("project_id"): row
            for row in (publication.rows if isinstance(publication.rows, list) else [])
            if isinstance(row, dict) and row.get("project_id")
        }
        snapshots = []
        for project_id in sorted(listed_ids):
            project = projects.get(project_id)
            row = rows.get(project_id, {})
            snapshots.append({
                "project_id": project_id,
                "title": project.title if project else row.get("title", ""),
                "team": project.team.name if project else row.get("team", ""),
                "team_id": project.team.public_id if project else row.get("team_id", project_id),
                "track": project.track.name if project and project.track_id else (
                    row.get("track") if row else None
                ),
                "track_id": project.track.public_id if project and project.track_id else (
                    row.get("track_id") if row else None
                ),
                "status": project.status if project else (
                    "submitted" if row.get("status") in (
                        "ranked", "unranked_no_reviews", "unranked_disconnected"
                    ) else row.get("status", "unknown")
                ),
                "status_reason": (project.status_reason or None) if project else (
                    row.get("status_reason") if row else None
                ),
            })

        inputs["project_snapshot"] = snapshots
        if "projects" not in inputs:
            inputs["projects"] = sorted(
                project_id for project_id, row in rows.items()
                if row.get("status") in ("ranked", "unranked_no_reviews")
            )
        inputs.setdefault("reviews_per_project", publication.event.reviews_per_project)
        params = inputs.get("params")
        if not isinstance(params, dict):
            params = {}
            inputs["params"] = params
        if isinstance(params, dict):
            params.setdefault("method", publication.method)
            params.setdefault("rubric_version", 0)
            publication_params = publication.params if isinstance(publication.params, dict) else {}
            params.setdefault("lam", publication_params.get("lam"))
            params.setdefault("lambda_source", publication_params.get("lambda_source", "fixed"))
            if "criteria" not in params:
                try:
                    rubric = publication.event.rubric
                except ObjectDoesNotExist:
                    params["criteria"] = []
                else:
                    params["criteria"] = [
                        {
                            "key": criterion.key,
                            "weight": float(criterion.weight),
                            "min_score": criterion.min_score,
                            "max_score": criterion.max_score,
                        }
                        for criterion in rubric.criteria.order_by("position", "id")
                    ]
        publication.inputs = inputs
        publication.input_digest = _digest_inputs(inputs)
        publication.project_snapshot_backfilled = True
        publication.save(
            using=alias,
            update_fields=["inputs", "input_digest", "project_snapshot_backfilled"],
        )


def _digest_inputs(inputs):
    import hashlib
    import json

    canonical = json.dumps(inputs, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assign_versions(apps, schema_editor):
    Publication = apps.get_model("results", "ResultPublication")
    alias = schema_editor.connection.alias
    event_ids = Publication.objects.using(alias).values_list("event_id", flat=True).distinct()
    for event_id in event_ids.iterator():
        ids = list(
            Publication.objects.using(alias)
            .filter(event_id=event_id)
            .order_by("published_at", "id")
            .values_list("id", flat=True)
        )
        for version, publication_id in enumerate(ids, start=1):
            Publication.objects.using(alias).filter(pk=publication_id).update(version=version)


class Migration(migrations.Migration):
    dependencies = [
        ("results", "0003_resultpublication_awards"),
        ("projects", "0001_initial"),
        ("events", "0005_event_pairwise_min_comparisons"),
        ("judging", "0003_comparison_identity_retraction"),
    ]

    operations = [
        migrations.AddField(
            model_name="resultpublication",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="resultpublication",
            name="project_snapshot_backfilled",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(backfill_project_snapshots, migrations.RunPython.noop),
        migrations.RunPython(assign_versions, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="resultpublication",
            constraint=models.UniqueConstraint(
                fields=("event", "version"),
                name="publication_unique_version_per_event",
            ),
        ),
    ]
