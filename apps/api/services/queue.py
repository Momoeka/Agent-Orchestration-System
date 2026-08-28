"""How the API hands a task to a worker. Celery in production; an in-memory list in tests."""

from __future__ import annotations

from typing import Protocol


class TaskQueue(Protocol):
    def enqueue(self, task_id: str) -> None: ...


class CeleryQueue:
    def enqueue(self, task_id: str) -> None:
        from packages.orchestrator.worker import TASK_NAME, celery_app

        celery_app.send_task(TASK_NAME, args=[task_id])


class InMemoryQueue:
    def __init__(self) -> None:
        self.items: list[str] = []

    def enqueue(self, task_id: str) -> None:
        self.items.append(task_id)
