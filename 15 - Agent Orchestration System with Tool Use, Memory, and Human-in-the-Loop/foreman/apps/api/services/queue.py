"""How the API hands work to a worker. Celery in production; in-memory lists in tests."""

from __future__ import annotations

from typing import Protocol


class TaskQueue(Protocol):
    def enqueue(self, task_id: str) -> None: ...

    def enqueue_resume(self, task_id: str, approval_id: int) -> None: ...


class CeleryQueue:
    def enqueue(self, task_id: str) -> None:
        from packages.orchestrator.worker import TASK_NAME, celery_app

        celery_app.send_task(TASK_NAME, args=[task_id])

    def enqueue_resume(self, task_id: str, approval_id: int) -> None:
        from packages.orchestrator.worker import RESUME_TASK_NAME, celery_app

        celery_app.send_task(RESUME_TASK_NAME, args=[task_id, approval_id])


class InMemoryQueue:
    def __init__(self) -> None:
        self.items: list[str] = []
        self.resumes: list[tuple[str, int]] = []

    def enqueue(self, task_id: str) -> None:
        self.items.append(task_id)

    def enqueue_resume(self, task_id: str, approval_id: int) -> None:
        self.resumes.append((task_id, approval_id))
