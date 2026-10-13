from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_safe


@require_safe
@ensure_csrf_cookie
def spa_index(request):
    """Serve the built React app; client-side routing handles the path.

    The shell contains no data: every API call requires authentication.
    """
    index = settings.FRONTEND_DIST_DIR / "index.html"
    try:
        html = index.read_text()
    except FileNotFoundError:
        return HttpResponse(
            "Frontend not built. Run `npm run build` in frontend/ "
            "(or use the Vite dev server, see README).",
            status=503,
            content_type="text/plain",
        )
    return HttpResponse(html)
