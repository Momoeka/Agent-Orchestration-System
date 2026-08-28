"""Foreman orchestration API (Architecture.md §11).

Served through the app factory so importing this module has no side effects:

    uv run uvicorn apps.api.main:create_app --factory --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI

from apps.api.config.settings import Settings, get_settings
from apps.api.middleware.auth import ApiKeyMiddleware
from apps.api.middleware.errors import install_error_handlers
from apps.api.routes import tasks
from apps.api.services.queue import CeleryQueue, TaskQueue
from apps.api.services.task_service import TaskService
from packages.orchestrator.memory.persistent import TaskStore
from packages.orchestrator.runtime import make_store
from packages.orchestrator.tracing.otel import configure_tracing


def create_app(
    settings: Settings | None = None,
    *,
    store: TaskStore | None = None,
    queue: TaskQueue | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_tracing(settings)
    app = FastAPI(title="Foreman", version="0.1.0")
    app.state.settings = settings
    app.state.task_service = TaskService(store or make_store(settings), queue or CeleryQueue())
    app.add_middleware(ApiKeyMiddleware, api_key=settings.api_key)
    install_error_handlers(app)
    app.include_router(tasks.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
