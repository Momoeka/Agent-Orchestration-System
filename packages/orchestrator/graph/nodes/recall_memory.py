"""Phase 2 stub — Phase 5 replaces this with the ChromaDB recall (Architecture.md §7.3)."""

from __future__ import annotations

from typing import Any

from packages.orchestrator.graph.state import TaskState, event
from packages.orchestrator.tracing.otel import span


async def recall_memory(state: TaskState) -> dict[str, Any]:
    with span("memory.recall", task_id=state["task_id"], count=0):
        return {
            "recalled_memories": [],
            "events": [event("memory", "no long-term memory yet (Phase 5)", node="recall_memory")],
        }
