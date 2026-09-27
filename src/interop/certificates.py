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
            "subject": project.team.name,
            "project": project.title,
            "people": [m.user.display_name or "Participant" for m in members],
            "owner_ids": [m.user_id for m in members],
            "issued_at": publication.published_at,
            "public_id": public_id,
            "detail": f"{award.get('prize', 'Winner')} — place {award.get('place', 1)}.",
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
    publication = policy.certificate_publications(event).first()
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
                                "subject": project.team.name, "project": project.title,
                                "people": [m.user.display_name or "Participant"
                                           for m in project.team.memberships.all()],
                                "detail": f"{award.get('prize', 'Winner')} — place {award.get('place', 1)}."})
    return entries
