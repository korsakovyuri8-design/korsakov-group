from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import chat, staff, whatsapp
from app.config import Settings, get_settings
from app.container import Container, build_container
from app.observability import configure_logging, log_event


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or (container.settings if container else get_settings())
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not hasattr(app.state, "container"):
            app.state.container = build_container(settings)
        log_event(
            "startup",
            env=settings.env,
            llm_provider=settings.llm_provider if app.state.container.llm else "none",
            whatsapp_transport=app.state.container.whatsapp.name,
            hotel=settings.hotel_slug,
        )
        yield
        app.state.container.engine.dispose()

    app = FastAPI(title="HOTELBOT", version="0.1.0", lifespan=lifespan)
    if container is not None:
        app.state.container = container

    app.include_router(whatsapp.router)
    app.include_router(chat.router)
    app.include_router(staff.router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log_event("error", where="http", path=request.url.path, error=type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    return app

# Run with:  uvicorn app.main:create_app --factory
