"""Celery worker: runs a task's graph with the Postgres checkpointer, resuming if a checkpoint exists.

    uv run celery -A packages.orchestrator.worker worker --pool=solo -l info

``execute_task`` is also callable directly (the API test and CLI use it without a broker).
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from celery import Celery
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from packages.orchestrator.graph.build_graph import build_graph
from packages.orchestrator.graph.state import initial_state
from packages.orchestrator.runtime import build_runtime, make_store
from packages.orchestrator.tracing.otel import configure_tracing, span
from packages.shared.asyncio_compat import use_selector_event_loop_on_windows
from packages.shared.config import Settings, get_settings
from packages.shared.types.task import TaskOptions, TaskStatus

log = structlog.get_logger(__name__)

_settings = get_settings()
celery_app = Celery("foreman", broker=_settings.redis_url, backend=_settings.redis_url)
celery_app.conf.update(task_track_started=True, worker_prefetch_multiplier=1, task_acks_late=True)

TASK_NAME = "foreman.run_task"


def checkpointer_conninfo(database_url: str) -> str:
    """LangGraph's saver wants a psycopg conninfo, not a SQLAlchemy URL."""
    return database_url.replace("postgresql+psycopg://", "postgresql://")


async def execute_task(task_id: str, *, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    provider = configure_tracing(settings)
    store = make_store(settings)
    row = store.get_task(task_id)
    if row is None:
        raise ValueError(f"unknown task {task_id}")

    deps = await build_runtime(settings, store=store)
    config: RunnableConfig = {"configurable": {"thread_id": task_id}}
    async with AsyncPostgresSaver.from_conn_string(
        checkpointer_conninfo(settings.database_url)
    ) as saver:
        await saver.setup()
        graph = build_graph(deps, checkpointer=saver)
        snapshot = await graph.aget_state(config)
        resuming = bool(snapshot.values) and bool(snapshot.next)
        graph_input = (
            None
            if resuming
            else initial_state(
                task_id, row.user_id, row.request, TaskOptions.model_validate(row.options or {})
            )
        )
        store.set_status(task_id, TaskStatus.RUNNING)
        log.info("task.start", task_id=task_id, resuming=resuming)
        with span("task", task_id=task_id, user_id=row.user_id, resuming=resuming):
            try:
                final = await graph.ainvoke(graph_input, config)
            except Exception as e:
                store.set_status(
                    task_id, TaskStatus.FAILED, error=f"{type(e).__name__}: {str(e)[:400]}"
                )
                log.error("task.crashed", task_id=task_id, error=str(e)[:300])
                raise
    provider.force_flush()
    log.info("task.finished", task_id=task_id, status=final.get("status"))
    return {"task_id": task_id, "status": final.get("status"), "error": final.get("error")}


@celery_app.task(name=TASK_NAME, bind=True, max_retries=0)  # type: ignore[untyped-decorator]
def run_task(self: Any, task_id: str) -> dict[str, Any]:
    use_selector_event_loop_on_windows()
    return asyncio.run(execute_task(task_id))
