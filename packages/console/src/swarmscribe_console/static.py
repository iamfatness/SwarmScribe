"""The built web app (C3), served by the console under its security headers (fleet console
spec 3, 7). An extensionless path that is not a file gets index.html, so the web app's own
routes survive a reload; /api/ and /auth/ never do, and StaticFiles keeps every path inside
the folder."""

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_NEVER_THE_APP = ("/api/", "/auth/")
_EXACTLY = ("/api", "/auth")


class SpaFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
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
            return await super().get_response("index.html", scope)
