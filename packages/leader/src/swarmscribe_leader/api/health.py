from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..db.migrate import current_revision

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    engine = request.app.state.engine
    try:
        async with engine.connect() as conn:
            await conn.scalar(text("select 1"))
        revision = await current_revision(engine)
    except Exception:
        return JSONResponse({"status": "database unreachable"}, status_code=503)
    if revision != request.app.state.head_revision:
        return JSONResponse({"status": "database migrations are not current"}, status_code=503)
    return JSONResponse({"status": "ready"})
