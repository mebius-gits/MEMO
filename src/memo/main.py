from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from memo.application.interaction import InteractionService
from memo.application.use_cases import EnvironmentService
from memo.domain.entities import Conflict, NotFound
from memo.infrastructure.database import build_engine
from memo.infrastructure.repository import SqlAlchemyRepository
from memo.infrastructure.settings import Settings
from memo.infrastructure.template_reply import TemplateReplyGenerator
from memo.presentation.interaction_routes import router as interaction_router
from memo.presentation.routes import router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = build_engine(settings.database_url)
        app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        app.state.service_factory = lambda session: EnvironmentService(
            SqlAlchemyRepository(session)
        )
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    app.include_router(interaction_router)
    app.state.interaction_service = InteractionService(TemplateReplyGenerator())

    @app.exception_handler(NotFound)
    async def not_found(_: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(Conflict)
    async def conflict(_: Request, exc: Conflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready(request: Request):
        try:
            with request.app.state.session_factory() as session:
                session.execute(text("SELECT id FROM devices LIMIT 1"))
                session.execute(text("SELECT message_id FROM readings LIMIT 1"))
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ready"}

    return app
