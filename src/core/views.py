"""Infrastructure pages: the container healthcheck and authorized media serving."""
import mimetypes
import posixpath
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.http import FileResponse, Http404, JsonResponse


def healthz(request):
    """Container healthcheck: proves the app is up *and* the database answers."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"ok": True})


def media(request, path: str):
    """Serve an uploaded image, but only to someone allowed to see its project.

    Media lives outside the static tree and is never served directly: the owning
    project is resolved first and projects.policy.visible_project decides
    (BUILD-SEC section 16). Everyone else gets a plain 404, not a 403, so the
    response never confirms that the file exists.
    """
    from projects.policy import project_owning_media, visible_project

    name = posixpath.normpath(path)
    if name.startswith(("/", "..")):
        raise Http404("No such file.")
    project = project_owning_media(name)
    if project is None or visible_project(request.user, project) is None:
        raise Http404("No such file.")
    full_path = (Path(settings.MEDIA_ROOT) / name).resolve()
    if not full_path.is_file() or Path(settings.MEDIA_ROOT).resolve() not in full_path.parents:
        raise Http404("No such file.")
    content_type = mimetypes.guess_type(full_path.name)[0] or "application/octet-stream"
    return FileResponse(full_path.open("rb"), content_type=content_type)
