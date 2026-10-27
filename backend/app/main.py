from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from redis import Redis
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory
from app.routers import agents, auth, models, operations, users


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application):
        yield
        application.state.engine.dispose()

    application = FastAPI(title="Domain Agent", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.engine = make_engine(settings.database_url)
    application.state.session_factory = make_session_factory(application.state.engine)
    application.state.resource_validators = {}
    application.state.group_grant_cleanups = []
    for router in (auth.router, users.router, models.router, agents.router, operations.router):
        application.include_router(router, prefix="/api/v1")

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc):
        errors = [{key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @application.exception_handler(IntegrityError)
    async def conflict(request: Request, exc):
        return JSONResponse({"detail": "Resource conflicts with existing data"}, status_code=409)

    @application.exception_handler(Exception)
    async def internal_error(request: Request, exc):
        # Never expose raw upstream responses, SQL parameters, or exception strings.
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    @application.get("/health/live")
    def live():
        return {"status": "ok"}

    @application.get("/health/ready")
    def ready():
        try:
            with application.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            if settings.redis_url:
                with Redis.from_url(
                    settings.redis_url, socket_connect_timeout=2, socket_timeout=2
                ) as redis:
                    redis.ping()
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ok"}

    dist = Path(settings.frontend_dist).resolve()
    if (dist / "assets").is_dir():
        application.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @application.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path == "api" or path.startswith("api/") or path.startswith("health/"):
            raise HTTPException(404, "Not found")
        candidate = (dist / path).resolve()
        if candidate.is_relative_to(dist) and candidate.is_file():
            return FileResponse(candidate)
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html")
        raise HTTPException(404, "Not found")

    return application


app = create_app()
