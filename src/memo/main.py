import asyncio
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from memo.application.interaction import InteractionService
from memo.application.use_cases import EnvironmentService
from memo.domain.entities import Conflict, NotFound
from memo.infrastructure.database import build_engine
from memo.infrastructure.model_reply import ModelReplyGenerator
from memo.infrastructure.notifications import run_dispatcher
from memo.infrastructure.repository import SqlAlchemyRepository
from memo.infrastructure.schedules import run_scheduler
from memo.infrastructure.settings import AttentionSettings, Settings
from memo.presentation.interaction_routes import device_router
from memo.presentation.interaction_routes import router as interaction_router
from memo.presentation.memory_routes import router as memory_router
from memo.presentation.routes import router
from memo.presentation.schedule_routes import router as schedule_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    timezone = AttentionSettings().timezone

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = build_engine(settings.database_url)
        app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        app.state.service_factory = lambda session: EnvironmentService(
            SqlAlchemyRepository(session)
        )
        stop = threading.Event()
        # The dispatcher turns queued notices into replies; the scheduler turns due jobs
        # into queued notices. Both are durable, so a restart resumes where they left off.
        workers = [
            threading.Thread(
                target=run_dispatcher,
                args=(
                    stop,
                    app.state.session_factory,
                    app.state.interaction_service,
                    settings.openai_timeout_seconds,
                    timezone,
                ),
                daemon=True,
            ),
            threading.Thread(
                target=run_scheduler, args=(stop, app.state.session_factory, timezone), daemon=True
            ),
        ]
        if settings.notifications_enabled:
            for worker in workers:
                worker.start()
        try:
            yield
        finally:
            stop.set()
            for worker in workers:
                if worker.is_alive():
                    await asyncio.to_thread(worker.join, settings.openai_timeout_seconds + 5)
            engine.dispose()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.include_router(router)
    app.include_router(interaction_router)
    app.include_router(device_router)
    app.include_router(memory_router)
    app.include_router(schedule_router)
    app.state.interaction_service = InteractionService(ModelReplyGenerator(settings))
    app.state.timezone = timezone

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
                session.execute(text("SELECT id FROM task_memory LIMIT 1"))
                session.execute(text("SELECT id FROM conversation_memory LIMIT 1"))
                session.execute(text("SELECT id FROM scheduled_jobs LIMIT 1"))
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ready"}

    return app
