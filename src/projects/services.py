"""Project writes: revision snapshots, digests and canonical JSON.

Kept separate from the importer so submissions made through the API and
revisions created at import time produce byte-identical receipts.
"""
import hashlib
import json

from projects.models import Project, ProjectRevision


def canonical_json(payload) -> str:
    """Stable JSON for hashing: sorted keys, no whitespace, no datetimes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def snapshot_digest(snapshot: dict) -> str:
    """sha256 of the canonical snapshot; the receipt a revision is identified by."""
    return hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()


def build_snapshot(project, answers: dict | None = None) -> dict:
    """All public and private field values plus answers, for a revision snapshot."""
    payload = project.snapshot()
    if answers is None:
        answers = {str(a.question_id): a.value for a in project.answers.all()}
    payload["answers"] = {key: answers[key] for key in sorted(answers)}
    return payload


def create_revision(project, *, snapshot: dict | None = None, actor=None) -> ProjectRevision:
    """Append a revision and advance project.revision.

    The caller owns the transaction and the submission-window check.
    """
    number = (project.revision or 0) + 1
    payload = snapshot if snapshot is not None else build_snapshot(project)
    revision = ProjectRevision.objects.create(
        project=project,
        number=number,
        snapshot=payload,
        digest=snapshot_digest(payload),
        created_by=actor,
    )
    Project.objects.filter(pk=project.pk).update(revision=number)
    project.revision = number
    return revision
