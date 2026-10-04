"""The built web app (C3), served by the console under its security headers (fleet console
spec 3, 7). An extensionless path that is not a file gets index.html, so the web app's own
routes survive a reload; /api/ and /auth/ never do, and StaticFiles keeps every path inside
the folder."""

import re

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_NEVER_THE_APP = ("/api/", "/auth/")
_EXACTLY = ("/api", "/auth")

# Cache-Control. Anything the browser must re-check (index.html, the fallback, a file whose
# name carries no content hash) is `no-cache`: it may be stored but is revalidated every
# time, so a deploy is seen at once. Only a name that looks hashed ("index-Dk3f9aB2.js": a
# dot or dash, then 8 or more letters, digits or underscores with at least one digit) is
# cached for a year, because a changed file gets a new name.
NO_CACHE = "no-cache"
IMMUTABLE = "public, max-age=31536000, immutable"
_HASHED = re.compile(r"[.-](?=[A-Za-z0-9_]*\d)[A-Za-z0-9_]{8,}\.[A-Za-z0-9]+")


def cache_control(path: str) -> str:
    last = path.rsplit("/", 1)[-1]
    if last in ("", ".", "index.html"):
        return NO_CACHE
    return IMMUTABLE if _HASHED.search(last) else NO_CACHE


class SpaFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = cache_control(path)
            return response
        except HTTPException as exc:
            last = path.rsplit("/", 1)[-1]
            full = scope["path"]
            if (
                exc.status_code != 404
                or full.startswith(_NEVER_THE_APP)
                or full in _EXACTLY
                or "." in last
                or ".." in path.split("/")
            ):
                raise
            fallback = await super().get_response("index.html", scope)
            fallback.headers["Cache-Control"] = NO_CACHE
            return fallback
