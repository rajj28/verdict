"""Read-only certificate projections and private verification codes."""
import hashlib
import hmac
from django.conf import settings

from interop import policy


def verification_code(event, kind: str, public_id: str) -> str:
    message = f"{event.slug}:{kind}:{public_id}".encode("utf-8")
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), message, hashlib.sha256).hexdigest()[:20]


def verify_code(event, kind: str, public_id: str, supplied: str) -> bool:
    if not isinstance(supplied, str) or not supplied.isascii():
        return False
    if certificate_data(event, kind, public_id) is None:
        return False
    return code_matches(verification_code(event, kind, public_id), supplied)


def code_matches(expected: str, supplied: str) -> bool:
    return isinstance(supplied, str) and supplied.isascii() and hmac.compare_digest(expected, supplied)


def _superseded_by_version(event, publication, award) -> int | None:
    publications = policy.certificate_publications(event).filter(
        version__gt=publication.version
    ).order_by("version")
    for later in publications:
        replacement = next(
            (
                row for row in later.awards
                if row.get("prize_id") == award.get("prize_id")
                and str(row.get("place")) == str(award.get("place"))
            ),
            None,
        )
        if replacement is None or replacement.get("project_id") != award.get("project_id"):
            return later.version
    return None


def certificate_data(event, kind: str, public_id: str) -> dict | None:
    if kind == "participation":
        project = (
            policy.certificate_project(event, public_id).first()
        )
        if project is None:
            return None
        members = list(
            policy.certificate_members(event, project.team)
        )
        return {
            "subject": project.team.name,
            "project": project.title,
            "people": [m.user.display_name or "Participant" for m in members],
            "owner_ids": [m.user_id for m in members],
            "issued_at": project.last_submitted_at,
            "public_id": project.public_id,
        }
    if kind == "judge":
        role = policy.certificate_judge(event, public_id).first()
        if role is None:
            return None
        count = policy.certificate_reviews(event, role).count()
        if count < 1:
            return None
        return {
            "subject": role.user.display_name or "Judge",
            "project": "",
            "people": [],
            "owner_ids": [role.user_id],
            "issued_at": None,
            "public_id": role.public_id,
            "detail": f"Submitted {count} review{'s' if count != 1 else ''}.",
        }
    if kind == "winner":
        parts = public_id.split(".")
        if len(parts) != 3 or not parts[2].isdigit():
            return None
        publication = policy.certificate_publications(event).filter(public_id=parts[0]).first()
        if publication is None:
            return None
        award = next(
            (row for row in publication.awards
             if row.get("prize_id") == parts[1] and str(row.get("place")) == parts[2]),
            None,
        )
        if award is None:
            return None
        project = policy.certificate_project(
            event, award.get("project_id", ""), submitted_only=False
        ).first()
        if project is None:
            return None
        members = list(
            policy.certificate_members(event, project.team)
        )
        return {
            "subject": award.get("team") or project.team.name,
            "project": award.get("project") or project.title,
            "people": [m.user.display_name or "Participant" for m in members],
            "owner_ids": [m.user_id for m in members],
            "issued_at": publication.published_at,
            "public_id": public_id,
            "publication_version": publication.version,
            "superseded_by_version": _superseded_by_version(event, publication, award),
            "detail": (
                f"Publication version {publication.version}: "
                f"{award.get('prize', 'Winner')} — place {award.get('place', 1)}."
            ),
        }
    return None


def certificate_index(event) -> list[dict]:
    entries = []
    projects = policy.certificate_award_projects(event)
    projects_by_id = {}
    for project in projects:
        projects_by_id[project.public_id] = project
        if project.status != "submitted":
            continue
        entries.append({"kind": "participation", "public_id": project.public_id,
                        "label": f"Participation — {project.title}", "subject": project.team.name,
                        "project": project.title,
                        "people": [m.user.display_name or "Participant"
                                   for m in project.team.memberships.all()]})
    roles = policy.certificate_judges(event)
    for role in roles:
        if role.submitted_reviews:
            entries.append({"kind": "judge", "public_id": role.public_id,
                            "label": f"Judge participation — {role.user.display_name or 'Judge'}",
                            "subject": role.user.display_name or "Judge", "people": [],
                            "detail": f"Submitted {role.submitted_reviews} reviews."})
    publication = policy.certificate_publications(event).order_by("-version").first()
    if publication:
        for award in publication.awards:
            project_id = award.get("project_id")
            prize_id = award.get("prize_id")
            place = award.get("place")
            if project_id and prize_id and place and project_id in projects_by_id:
                project = projects_by_id[project_id]
                public_id = f"{publication.public_id}.{prize_id}.{place}"
                entries.append({"kind": "winner", "public_id": public_id,
                                "label": f"Winner — {award.get('project', project_id)} ({award.get('prize', '')})",
                                "subject": award.get("team") or project.team.name,
                                "project": award.get("project") or project.title,
                                "people": [m.user.display_name or "Participant"
                                           for m in project.team.memberships.all()],
                                "publication_version": publication.version,
                                "detail": (
                                    f"Publication version {publication.version}: "
                                    f"{award.get('prize', 'Winner')} — place {award.get('place', 1)}."
                                )})
    return entries
