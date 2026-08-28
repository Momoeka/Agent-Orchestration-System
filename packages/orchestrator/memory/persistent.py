"""Tier 2 — PostgreSQL system of record (Architecture.md §7.2): tables and the repositories that
are the ONLY code that touches them (Rules.md §1, §3)."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from packages.orchestrator.memory.db import Base
from packages.shared.types.cost import CostEntry
from packages.shared.types.deliverable import Deliverable
from packages.shared.types.gate import ToolEvent
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.review import ReviewVerdict
from packages.shared.types.subtask import SubtaskResult
from packages.shared.types.task import TaskEvent, TaskOptions, TaskStatus


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _json(model: Any) -> Any:
    return json.loads(model.model_dump_json()) if model is not None else None


class TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    request: Mapped[str] = mapped_column(Text)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), index=True, default=TaskStatus.QUEUED.value)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    final_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class SubtaskRow(Base):
    __tablename__ = "subtasks"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # f"{task_id}:{subtask_id}"
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    subtask_id: Mapped[str] = mapped_column(String(16))
    specialist: Mapped[str] = mapped_column(String(32))
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    verdict: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class LLMCallRow(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(96))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ToolInvocationRow(Base):
    __tablename__ = "tool_invocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    subtask_id: Mapped[str] = mapped_column(String(16))
    agent: Mapped[str] = mapped_column(String(32))
    tool: Mapped[str] = mapped_column(String(64), index=True)
    args_hash: Mapped[str] = mapped_column(String(32))
    risk: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decision: Mapped[str] = mapped_column(String(16), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    result_size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OutboxRow(Base):
    """External actions the agents *proposed*. Nothing here is ever sent by Foreman (PRD.md §4)."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="queued_for_human")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLogRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TaskStore:
    """Synchronous repository. Async callers wrap calls in ``asyncio.to_thread``."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sessions = session_factory

    # ---------- tasks ----------

    def create_task(self, *, user_id: str, request: str, options: TaskOptions) -> TaskRow:
        row = TaskRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            request=request,
            options=_json(options),
            status=TaskStatus.QUEUED.value,
        )
        with self._sessions() as s:
            s.add(row)
            s.add(AuditLogRow(task_id=row.id, actor=user_id, action="task.created", payload={}))
            s.commit()
            s.refresh(row)
        return row

    def get_task(self, task_id: str) -> TaskRow | None:
        with self._sessions() as s:
            return s.get(TaskRow, task_id)

    def set_status(
        self, task_id: str, status: TaskStatus, *, error: str | None = None, actor: str = "worker"
    ) -> None:
        with self._sessions() as s:
            row = s.get(TaskRow, task_id)
            if row is None:
                return
            row.status = status.value
            if error is not None:
                row.error = error
            s.add(
                AuditLogRow(
                    task_id=task_id,
                    actor=actor,
                    action=f"task.{status.value}",
                    payload={"error": error},
                )
            )
            s.commit()

    def set_plan(self, task_id: str, plan: ExecutionPlan) -> None:
        with self._sessions() as s:
            row = s.get(TaskRow, task_id)
            if row is None:
                return
            row.plan = _json(plan)
            for sub in plan.subtasks:
                s.merge(
                    SubtaskRow(
                        id=f"{task_id}:{sub.id}",
                        task_id=task_id,
                        subtask_id=sub.id,
                        specialist=sub.specialist.value,
                        spec=_json(sub),
                        status="planned",
                    )
                )
            s.commit()

    def finish_task(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        deliverable: Deliverable | None,
        cost_usd: float | None,
        error: str | None,
        results: dict[str, SubtaskResult],
        verdicts: dict[str, ReviewVerdict],
        cost_entries: list[CostEntry],
        events: list[TaskEvent],
        tool_events: list[ToolEvent] | None = None,
    ) -> None:
        with self._sessions() as s:
            row = s.get(TaskRow, task_id)
            if row is None:
                return
            row.status = status.value
            row.final_output = _json(deliverable)
            row.cost_usd = cost_usd
            row.error = error
            for sid, result in results.items():
                verdict = verdicts.get(sid)
                existing = s.get(SubtaskRow, f"{task_id}:{sid}")
                if existing is None:
                    existing = SubtaskRow(
                        id=f"{task_id}:{sid}",
                        task_id=task_id,
                        subtask_id=sid,
                        specialist="",
                        spec={},
                        status="",
                    )
                    s.add(existing)
                existing.attempt = result.attempt
                existing.result = _json(result)
                existing.verdict = _json(verdict)
                accepted = (
                    verdict is not None and verdict.accept and verdict.attempt == result.attempt
                )
                existing.status = (
                    "accepted"
                    if accepted
                    else "rejected"
                    if verdict is not None
                    else result.status.value
                )
            for c in cost_entries:
                s.add(
                    LLMCallRow(
                        task_id=task_id,
                        role=c.role,
                        provider=c.provider,
                        model=c.model,
                        input_tokens=c.input_tokens,
                        output_tokens=c.output_tokens,
                        cost_usd=c.cost_usd,
                        latency_ms=c.latency_ms,
                        fallback=c.fallback,
                    )
                )
            for t in tool_events or []:
                s.add(
                    ToolInvocationRow(
                        task_id=task_id,
                        subtask_id=t.subtask_id,
                        agent=t.agent,
                        tool=t.tool,
                        args_hash=t.args_hash,
                        risk=t.risk.value if t.risk else None,
                        decision=t.decision.value,
                        reason=t.reason[:2000],
                        ok=t.ok,
                        latency_ms=t.latency_ms,
                        result_size=t.result_size,
                    )
                )
            for e in events:
                s.add(
                    AuditLogRow(
                        task_id=task_id,
                        actor=e.node or "graph",
                        action=e.kind,
                        payload={"message": e.message, **e.data},
                    )
                )
            s.add(
                AuditLogRow(
                    task_id=task_id,
                    actor="worker",
                    action=f"task.{status.value}",
                    payload={"error": error},
                )
            )
            s.commit()

    # ---------- reads for the API ----------

    def task_view(self, task_id: str) -> dict[str, Any] | None:
        with self._sessions() as s:
            row = s.get(TaskRow, task_id)
            if row is None:
                return None
            subs = s.scalars(select(SubtaskRow).where(SubtaskRow.task_id == task_id)).all()
            calls = s.scalars(select(LLMCallRow).where(LLMCallRow.task_id == task_id)).all()
            tools = s.scalars(
                select(ToolInvocationRow).where(ToolInvocationRow.task_id == task_id)
            ).all()
            return {
                "task_id": row.id,
                "user_id": row.user_id,
                "request": row.request,
                "status": row.status,
                "plan": row.plan,
                "subtasks": [
                    {
                        "id": x.subtask_id,
                        "specialist": x.specialist,
                        "status": x.status,
                        "attempt": x.attempt,
                        "result": x.result,
                        "verdict": x.verdict,
                    }
                    for x in sorted(subs, key=lambda x: x.subtask_id)
                ],
                "final_output": row.final_output,
                "cost_usd": row.cost_usd,
                "llm_calls": len(calls),
                "tokens": sum(c.input_tokens + c.output_tokens for c in calls),
                "tool_calls": len(tools),
                "tool_calls_not_executed": sum(1 for t in tools if t.decision != "allow"),
                "error": row.error,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }


class OutboxRepository:
    """Written by the actions MCP server. Read by humans (Phase 4 UI). Never drained automatically."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sessions = session_factory

    def add(self, kind: str, payload: dict[str, Any], *, task_id: str | None = None) -> int:
        with self._sessions() as s:
            row = OutboxRow(kind=kind, payload=payload, task_id=task_id)
            s.add(row)
            s.commit()
            s.refresh(row)
            return int(row.id)

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._sessions() as s:
            rows = s.scalars(select(OutboxRow).order_by(OutboxRow.id.desc()).limit(limit)).all()
            return [
                {
                    "id": r.id,
                    "task_id": r.task_id,
                    "kind": r.kind,
                    "payload": r.payload,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
