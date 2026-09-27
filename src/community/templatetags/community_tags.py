"""Template inclusion for the read-only public project discussion."""
from django import template

from community.policy import can_manage_voting, visible_comments

register = template.Library()


@register.inclusion_tag("community/comments.html", takes_context=True)
def project_comments(context, project):
    request = context["request"]
    available = project.status == "submitted" and project.event.gallery_public
    return {
        "project": project,
        "comments": visible_comments(request.user, project) if available else [],
        "available": available,
        "can_comment": available and request.user.is_authenticated,
        "can_moderate": can_manage_voting(request.user, project.event),
    }
