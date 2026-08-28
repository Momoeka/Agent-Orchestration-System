"""Celery worker: runs a task's graph with the Postgres checkpointer, pauses when the graph raises
an approval, resumes with a human's decision, and expires overdue approvals on a schedule.

    uv run celery -A packages.orchestrator.worker worker -B --pool=solo -l info

``execute_task`` is also callable directly (tests and the CLI use it without a broker).
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from celery import Celery
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from packages.orchestrator.graph.build_graph import build_graph
from packages.orchestrator.graph.serde import checkpoint_serde
from packages.orchestrator.graph.state import initial_state
from packages.orchestrator.hitl.timeouts import expire_due
from packages.orchestrator.runtime import build_runtime, make_approvals, make_policy, make_store
from packages.orchestrator.tracing.otel import configure_tracing, span
from packages.shared.asyncio_compat import use_selector_event_loop_on_windows
from packages.shared.config import Settings, get_settings
from packages.shared.types.approval import ApprovalDecision
from packages.shared.types.cost import CostEntry
from packages.shared.types.gate import ToolEvent
from packages.shared.types.review import ReviewVerdict
from packages.shared.types.subtask import SubtaskResult
from packages.shared.types.task import TaskOptions, TaskStatus

log = structlog.get_logger(__name__)

_settings = get_settings()
celery_app = Celery("foreman", broker=_settings.redis_url, backend=_settings.redis_url)
celery_app.conf.update(
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    beat_schedule={
        "expire-approvals": {"task": "foreman.expire_approvals", "schedule": 60.0},
    },
)

TASK_NAME = "foreman.run_task"
RESUME_TASK_NAME = "foreman.resume_task"
EXPIRE_TASK_NAME = "foreman.expire_approvals"


def checkpointer_conninfo(database_url: str) -> str:
    """LangGraph's saver wants a psycopg conninfo, not a SQLAlchemy URL."""
    return database_url.replace("postgresql+psycopg://", "postgresql://")


def interrupt_payload(final: dict[str, Any]) -> dict[str, Any] | None:
    """The approval the graph is waiting on, if it stopped at an interrupt."""
    interrupts = final.get("__interrupt__") or []
    if not interrupts:
        return None
    value = getattr(interrupts[0], "value", interrupts[0])
    return dict(value) if isinstance(value, dict) else {"value": value}


async def execute_task(
    task_id: str, *, settings: Settings | None = None, resume: ApprovalDecision | None = None
) -> dict[str, Any]:
    settings = settings or get_settings()
    provider = configure_tracing(settings)
    store = make_store(settings)
    row = store.get_task(task_id)
    if row is None:
        raise ValueError(f"unknown task {task_id}")

    deps = await build_runtime(settings, store=store)
    config: RunnableConfig = {"configurable": {"thread_id": task_id}}
    async with AsyncPostgresSaver.from_conn_string(
        checkpointer_conninfo(settings.database_url), serde=checkpoint_serde()
    ) as saver:
        await saver.setup()
        graph = build_graph(deps, checkpointer=saver)
        snapshot = await graph.aget_state(config)
        resuming = bool(snapshot.values) and bool(snapshot.next)
        graph_input: Any
        if resume is not None:
            graph_input = Command(resume=resume.model_dump(mode="json"))
        elif resuming:
            graph_input = None
        else:
            graph_input = initial_state(
                task_id, row.user_id, row.request, TaskOptions.model_validate(row.options or {})
            )
        store.set_status(task_id, TaskStatus.RUNNING)
        log.info("task.start", task_id=task_id, resuming=resuming, with_decision=resume is not None)
        with span(
            "task", task_id=task_id, user_id=row.user_id, resuming=resuming, decision=bool(resume)
        ):
            try:
                final = await graph.ainvoke(graph_input, config)
            except Exception as e:
                store.set_status(
                    task_id, TaskStatus.FAILED, error=f"{type(e).__name__}: {str(e)[:400]}"
                )
                log.error("task.crashed", task_id=task_id, error=str(e)[:300])
                raise
    provider.force_flush()

    waiting = interrupt_payload(final)
    if waiting is not None:
        store.record_progress(
            task_id,
            results={
                k: SubtaskResult.model_validate(v)
                for k, v in (final.get("subtask_results") or {}).items()
                if v is not None
            },
            verdicts={
                k: ReviewVerdict.model_validate(v)
                for k, v in (final.get("review_verdicts") or {}).items()
                if v is not None
            },
            cost_entries=[CostEntry.model_validate(c) for c in final.get("cost_ledger") or []],
            tool_events=[ToolEvent.model_validate(t) for t in final.get("tool_events") or []],
        )
        store.set_status(task_id, TaskStatus.AWAITING_APPROVAL)
        log.info("task.awaiting_approval", task_id=task_id, approval_id=waiting.get("approval_id"))
        return {
            "task_id": task_id,
            "status": TaskStatus.AWAITING_APPROVAL.value,
            "approval_id": waiting.get("approval_id"),
            "level": waiting.get("level"),
            "kind": waiting.get("kind"),
        }
    log.info("task.finished", task_id=task_id, status=final.get("status"))
    return {"task_id": task_id, "status": final.get("status"), "error": final.get("error")}


@celery_app.task(name=TASK_NAME, bind=True, max_retries=0)  # type: ignore[untyped-decorator]
def run_task(self: Any, task_id: str) -> dict[str, Any]:
    use_selector_event_loop_on_windows()
    return asyncio.run(execute_task(task_id))


@celery_app.task(name=RESUME_TASK_NAME, bind=True, max_retries=0)  # type: ignore[untyped-decorator]
def resume_task(self: Any, task_id: str, approval_id: int) -> dict[str, Any]:
    use_selector_event_loop_on_windows()
    decision = make_approvals(_settings).decision_of(int(approval_id))
    if decision is None:
        raise ValueError(f"approval {approval_id} has no decision to resume with")
    return asyncio.run(execute_task(task_id, resume=decision))


@celery_app.task(name=EXPIRE_TASK_NAME, bind=True, max_retries=0)  # type: ignore[untyped-decorator]
def expire_approvals(self: Any) -> list[tuple[str, int]]:
    expired = expire_due(make_approvals(_settings), make_policy(_settings))
    for task_id, approval_id in expired:
        celery_app.send_task(RESUME_TASK_NAME, args=[task_id, approval_id])
    return expired
